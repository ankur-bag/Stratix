from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, cast

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.domain.background_jobs import (
    ACTIVE_JOB_STATUSES,
    BackgroundJob,
    BackgroundJobStatus,
    JobType,
    MonitoringSchedule,
    NewBackgroundJob,
    Notification,
    NotificationSeverity,
    NotificationType,
    ServiceInstance,
    ServiceType,
)
from app.domain.onboarding import new_id
from app.repositories.interfaces import RepositoryConflictError, RepositoryUnavailableError

Document = dict[str, Any]


def _document(
    model: BackgroundJob | MonitoringSchedule | Notification | ServiceInstance,
) -> Document:
    document = model.model_dump(mode="python")
    document["_id"] = document.pop("id", document.pop("instance_id", None))
    return document


def _model[T](model: type[T], document: Document | None) -> T | None:
    if document is None:
        return None
    value = dict(document)
    if model is ServiceInstance:
        value["instance_id"] = value.pop("_id")
    else:
        value["id"] = value.pop("_id")
    return cast(T, model.model_validate(value))  # type: ignore[attr-defined]


class MongoBackgroundJobRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def enqueue(self, *, job: NewBackgroundJob) -> BackgroundJob:
        now = job.available_at
        record = BackgroundJob(
            id=new_id(),
            **job.model_dump(),
            created_at=now,
            updated_at=now,
        )
        try:
            await self._collection.insert_one(_document(record))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return record

    async def get_by_id(self, organization_id: str, job_id: str) -> BackgroundJob | None:
        try:
            document = await self._collection.find_one(
                {"_id": job_id, "organization_id": organization_id}
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(BackgroundJob, document)

    async def get_active_by_deduplication_key(
        self, organization_id: str, deduplication_key: str
    ) -> BackgroundJob | None:
        try:
            document = await self._collection.find_one(
                {
                    "organization_id": organization_id,
                    "deduplication_key": deduplication_key,
                    "status": {"$in": [value.value for value in ACTIVE_JOB_STATUSES]},
                }
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(BackgroundJob, document)

    async def get_by_deduplication_key(
        self, organization_id: str, deduplication_key: str
    ) -> BackgroundJob | None:
        try:
            document = await self._collection.find_one(
                {
                    "organization_id": organization_id,
                    "deduplication_key": deduplication_key,
                },
                sort=[("created_at", DESCENDING), ("_id", DESCENDING)],
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(BackgroundJob, document)

    async def claim_next(
        self,
        *,
        worker_id: str,
        supported_job_types: set[JobType],
        now: datetime,
        lease_duration: timedelta,
        excluded_organization_id: str | None = None,
    ) -> BackgroundJob | None:
        query: Document = {
            "status": {
                "$in": [
                    BackgroundJobStatus.QUEUED.value,
                    BackgroundJobStatus.RETRY_SCHEDULED.value,
                ]
            },
            "available_at": {"$lte": now},
            "job_type": {"$in": [item.value for item in supported_job_types]},
        }
        if excluded_organization_id:
            query["organization_id"] = {"$ne": excluded_organization_id}
        update = {
            "$set": {
                "status": BackgroundJobStatus.CLAIMED.value,
                "worker_id": worker_id,
                "last_worker_id": worker_id,
                "claimed_at": now,
                "heartbeat_at": now,
                "lease_expires_at": now + lease_duration,
                "updated_at": now,
            },
            "$inc": {"attempt_count": 1},
        }
        try:
            document = await self._collection.find_one_and_update(
                query,
                update,
                sort=[
                    ("priority", DESCENDING),
                    ("available_at", ASCENDING),
                    ("created_at", ASCENDING),
                ],
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(BackgroundJob, document)

    async def renew_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> bool:
        try:
            result = await self._collection.update_one(
                {
                    "_id": job_id,
                    "worker_id": worker_id,
                    "status": {
                        "$in": [
                            BackgroundJobStatus.CLAIMED.value,
                            BackgroundJobStatus.RUNNING.value,
                            BackgroundJobStatus.CANCEL_REQUESTED.value,
                        ]
                    },
                },
                {
                    "$set": {
                        "heartbeat_at": now,
                        "lease_expires_at": now + lease_duration,
                        "updated_at": now,
                    }
                },
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return result.modified_count == 1

    async def mark_running(
        self, *, job_id: str, worker_id: str, now: datetime
    ) -> BackgroundJob | None:
        return await self._owned_update(
            job_id,
            worker_id,
            {BackgroundJobStatus.CLAIMED},
            {
                "status": BackgroundJobStatus.RUNNING,
                "started_at": now,
                "stage": "running",
                "message": "Background job is running.",
                "updated_at": now,
            },
        )

    async def update_progress(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        progress_percent: int,
        stage: str,
        message: str,
    ) -> BackgroundJob | None:
        return await self._owned_update(
            job_id,
            worker_id,
            {BackgroundJobStatus.RUNNING, BackgroundJobStatus.CANCEL_REQUESTED},
            {
                "progress_percent": progress_percent,
                "stage": stage,
                "message": message,
                "updated_at": now,
            },
        )

    async def mark_succeeded(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        status: BackgroundJobStatus,
        stage: str,
        message: str,
        result_summary: Mapping[str, Any],
    ) -> BackgroundJob | None:
        return await self._owned_update(
            job_id,
            worker_id,
            {BackgroundJobStatus.RUNNING},
            {
                "status": status,
                "progress_percent": 100,
                "stage": stage,
                "message": message,
                "result_summary": dict(result_summary),
                "completed_at": now,
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
            },
        )

    async def schedule_retry(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        available_at: datetime,
        error_code: str,
        error_message: str,
        error_class: str,
    ) -> BackgroundJob | None:
        return await self._owned_update(
            job_id,
            worker_id,
            {BackgroundJobStatus.RUNNING, BackgroundJobStatus.CLAIMED},
            {
                "status": BackgroundJobStatus.RETRY_SCHEDULED,
                "available_at": available_at,
                "stage": "retry_scheduled",
                "message": "The job will retry after a temporary failure.",
                "error_code": error_code,
                "error_message": error_message,
                "error_class": error_class,
                "retryable": True,
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
            },
        )

    async def mark_failed(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        error_code: str,
        error_message: str,
        error_class: str,
    ) -> BackgroundJob | None:
        return await self._owned_update(
            job_id,
            worker_id,
            {BackgroundJobStatus.RUNNING, BackgroundJobStatus.CLAIMED},
            {
                "status": BackgroundJobStatus.FAILED,
                "stage": "failed",
                "message": error_message,
                "error_code": error_code,
                "error_message": error_message,
                "error_class": error_class,
                "retryable": False,
                "completed_at": now,
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
            },
        )

    async def mark_dead_lettered(
        self,
        *,
        job_id: str,
        worker_id: str | None,
        now: datetime,
        error_code: str,
        error_message: str,
        error_class: str,
    ) -> BackgroundJob | None:
        query: Document = {"_id": job_id}
        if worker_id is not None:
            query["worker_id"] = worker_id
        try:
            document = await self._collection.find_one_and_update(
                query,
                {
                    "$set": {
                        "status": BackgroundJobStatus.DEAD_LETTERED.value,
                        "stage": "dead_lettered",
                        "message": error_message,
                        "error_code": error_code,
                        "error_message": error_message,
                        "error_class": error_class,
                        "retryable": False,
                        "dead_lettered_at": now,
                        "completed_at": now,
                        "worker_id": None,
                        "lease_expires_at": None,
                        "updated_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(BackgroundJob, document)

    async def request_cancellation(
        self, organization_id: str, job_id: str, now: datetime
    ) -> BackgroundJob | None:
        try:
            document = await self._collection.find_one_and_update(
                {
                    "_id": job_id,
                    "organization_id": organization_id,
                    "status": {"$in": [item.value for item in ACTIVE_JOB_STATUSES]},
                },
                [
                    {
                        "$set": {
                            "status": {
                                "$cond": [
                                    {
                                        "$in": [
                                            "$status",
                                            [
                                                BackgroundJobStatus.QUEUED.value,
                                                BackgroundJobStatus.RETRY_SCHEDULED.value,
                                            ],
                                        ]
                                    },
                                    BackgroundJobStatus.CANCELLED.value,
                                    BackgroundJobStatus.CANCEL_REQUESTED.value,
                                ]
                            },
                            "cancelled_at": {
                                "$cond": [
                                    {
                                        "$in": [
                                            "$status",
                                            [
                                                BackgroundJobStatus.QUEUED.value,
                                                BackgroundJobStatus.RETRY_SCHEDULED.value,
                                            ],
                                        ]
                                    },
                                    now,
                                    "$cancelled_at",
                                ]
                            },
                            "updated_at": now,
                        }
                    }
                ],
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(BackgroundJob, document)

    async def mark_cancelled(
        self, *, job_id: str, worker_id: str | None, now: datetime
    ) -> BackgroundJob | None:
        query: Document = {
            "_id": job_id,
            "status": BackgroundJobStatus.CANCEL_REQUESTED.value,
        }
        if worker_id is not None:
            query["worker_id"] = worker_id
        try:
            document = await self._collection.find_one_and_update(
                query,
                {
                    "$set": {
                        "status": BackgroundJobStatus.CANCELLED.value,
                        "stage": "cancelled",
                        "message": "The job was cancelled at a safe checkpoint.",
                        "cancelled_at": now,
                        "completed_at": now,
                        "worker_id": None,
                        "lease_expires_at": None,
                        "updated_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(BackgroundJob, document)

    async def recover_expired_leases(self, *, now: datetime, limit: int) -> list[BackgroundJob]:
        recovered: list[BackgroundJob] = []
        try:
            cursor = (
                self._collection.find(
                    {
                        "status": {
                            "$in": [
                                BackgroundJobStatus.CLAIMED.value,
                                BackgroundJobStatus.RUNNING.value,
                                BackgroundJobStatus.CANCEL_REQUESTED.value,
                            ]
                        },
                        "lease_expires_at": {"$lt": now},
                    }
                )
                .sort("lease_expires_at", ASCENDING)
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
            for raw in documents:
                exhausted = int(raw.get("attempt_count", 0)) >= int(raw.get("max_attempts", 1))
                target = (
                    BackgroundJobStatus.DEAD_LETTERED
                    if exhausted
                    else BackgroundJobStatus.RETRY_SCHEDULED
                )
                changes: Document = {
                    "status": target.value,
                    "stage": target.value,
                    "message": (
                        "The job exhausted its attempts after a lost worker lease."
                        if exhausted
                        else "The job was recovered after its worker lease expired."
                    ),
                    "available_at": now,
                    "last_worker_id": raw.get("worker_id"),
                    "worker_id": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                    "error_code": "JOB_LEASE_EXPIRED",
                    "error_message": "The previous worker stopped reporting progress.",
                    "error_class": "LeaseExpired",
                    "retryable": not exhausted,
                }
                if exhausted:
                    changes["dead_lettered_at"] = now
                    changes["completed_at"] = now
                updated = await self._collection.find_one_and_update(
                    {
                        "_id": raw["_id"],
                        "worker_id": raw.get("worker_id"),
                        "lease_expires_at": raw.get("lease_expires_at"),
                    },
                    {"$set": changes},
                    return_document=ReturnDocument.AFTER,
                )
                model = _model(BackgroundJob, updated)
                if model is not None:
                    recovered.append(model)
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return recovered

    async def list(
        self,
        organization_id: str,
        *,
        status: BackgroundJobStatus | None,
        cursor: str | None,
        limit: int,
    ) -> list[BackgroundJob]:
        query: Document = {"organization_id": organization_id}
        if status is not None:
            query["status"] = status.value
        if cursor:
            query["_id"] = {"$lt": cursor}
        try:
            documents = (
                await self._collection.find(query)
                .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
                .limit(limit)
                .to_list(length=limit)
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return [cast(BackgroundJob, _model(BackgroundJob, item)) for item in documents]

    async def count_active_for_organization(self, organization_id: str) -> int:
        try:
            return await self._collection.count_documents(
                {
                    "organization_id": organization_id,
                    "status": {"$in": [item.value for item in ACTIVE_JOB_STATUSES]},
                }
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc

    async def count_queued(
        self, *, organization_id: str | None = None, scan_only: bool = False
    ) -> int:
        query: Document = {
            "status": {
                "$in": [
                    BackgroundJobStatus.QUEUED.value,
                    BackgroundJobStatus.RETRY_SCHEDULED.value,
                ]
            }
        }
        if organization_id:
            query["organization_id"] = organization_id
        if scan_only:
            query["job_type"] = {
                "$in": [
                    JobType.COMPETITOR_BASELINE_SCAN.value,
                    JobType.COMPETITOR_RESCAN.value,
                ]
            }
        try:
            return await self._collection.count_documents(query)
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc

    async def operational_counts(self, now: datetime) -> tuple[int | None, int, int]:
        try:
            oldest = await self._collection.find_one(
                {
                    "status": {
                        "$in": [
                            BackgroundJobStatus.QUEUED.value,
                            BackgroundJobStatus.RETRY_SCHEDULED.value,
                        ]
                    }
                },
                sort=[("available_at", ASCENDING)],
            )
            age = (
                max(0, int((now - oldest["available_at"]).total_seconds()))
                if oldest is not None
                else None
            )
            expired = await self._collection.count_documents(
                {
                    "status": {
                        "$in": [
                            BackgroundJobStatus.CLAIMED.value,
                            BackgroundJobStatus.RUNNING.value,
                        ]
                    },
                    "lease_expires_at": {"$lt": now},
                }
            )
            dead = await self._collection.count_documents(
                {"status": BackgroundJobStatus.DEAD_LETTERED.value}
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return age, expired, dead

    async def cleanup_terminal(
        self,
        organization_id: str,
        *,
        succeeded_before: datetime,
        failed_before: datetime,
        limit: int,
    ) -> int:
        query: Document = {
            "organization_id": organization_id,
            "$or": [
                {
                    "status": {
                        "$in": [
                            BackgroundJobStatus.SUCCEEDED.value,
                            BackgroundJobStatus.PARTIALLY_SUCCEEDED.value,
                            BackgroundJobStatus.CANCELLED.value,
                        ]
                    },
                    "completed_at": {"$lt": succeeded_before},
                },
                {
                    "status": {
                        "$in": [
                            BackgroundJobStatus.FAILED.value,
                            BackgroundJobStatus.DEAD_LETTERED.value,
                        ]
                    },
                    "completed_at": {"$lt": failed_before},
                },
            ],
        }
        try:
            documents = (
                await self._collection.find(query, {"_id": 1})
                .sort("completed_at", ASCENDING)
                .limit(limit)
                .to_list(length=limit)
            )
            if not documents:
                return 0
            result = await self._collection.delete_many(
                {"_id": {"$in": [item["_id"] for item in documents]}}
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return result.deleted_count

    async def _owned_update(
        self,
        job_id: str,
        worker_id: str,
        statuses: set[BackgroundJobStatus],
        changes: Mapping[str, Any],
    ) -> BackgroundJob | None:
        serialized = {
            key: value.value if isinstance(value, BackgroundJobStatus) else value
            for key, value in changes.items()
        }
        try:
            document = await self._collection.find_one_and_update(
                {
                    "_id": job_id,
                    "worker_id": worker_id,
                    "status": {"$in": [item.value for item in statuses]},
                },
                {"$set": serialized},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(BackgroundJob, document)


class MongoMonitoringScheduleRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, schedule: MonitoringSchedule) -> MonitoringSchedule:
        try:
            await self._collection.insert_one(_document(schedule))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return schedule

    async def get_for_competitor(
        self, organization_id: str, competitor_id: str
    ) -> MonitoringSchedule | None:
        try:
            document = await self._collection.find_one(
                {"organization_id": organization_id, "competitor_id": competitor_id}
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(MonitoringSchedule, document)

    async def update(
        self, organization_id: str, schedule_id: str, changes: Mapping[str, Any]
    ) -> MonitoringSchedule | None:
        serialized = {
            key: value.value if hasattr(value, "value") else value for key, value in changes.items()
        }
        try:
            document = await self._collection.find_one_and_update(
                {"_id": schedule_id, "organization_id": organization_id},
                {"$set": serialized},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(MonitoringSchedule, document)

    async def reserve_due(
        self,
        *,
        scheduler_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> MonitoringSchedule | None:
        try:
            document = await self._collection.find_one_and_update(
                {
                    "is_enabled": True,
                    "cadence": {"$ne": "manual"},
                    "next_run_at": {"$lte": now},
                    "$or": [
                        {"scheduler_lease_expires_at": None},
                        {"scheduler_lease_expires_at": {"$lt": now}},
                    ],
                },
                {
                    "$set": {
                        "scheduler_lease_owner": scheduler_id,
                        "scheduler_lease_expires_at": now + lease_duration,
                        "updated_at": now,
                    }
                },
                sort=[("next_run_at", ASCENDING), ("_id", ASCENDING)],
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(MonitoringSchedule, document)

    async def release_reservation(
        self,
        *,
        schedule_id: str,
        scheduler_id: str,
        now: datetime,
        next_run_at: datetime | None,
        job_id: str | None,
        degraded_reason: str | None = None,
    ) -> MonitoringSchedule | None:
        changes: Document = {
            "scheduler_lease_owner": None,
            "scheduler_lease_expires_at": None,
            "next_run_at": next_run_at,
            "last_scheduled_at": now if job_id else None,
            "last_job_id": job_id,
            "degraded_reason": degraded_reason,
            "updated_at": now,
        }
        try:
            document = await self._collection.find_one_and_update(
                {"_id": schedule_id, "scheduler_lease_owner": scheduler_id},
                {"$set": changes},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(MonitoringSchedule, document)


class MongoNotificationRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def create(self, notification: Notification) -> Notification:
        try:
            await self._collection.insert_one(_document(notification))
        except DuplicateKeyError as exc:
            raise RepositoryConflictError from exc
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return notification

    async def list(
        self,
        organization_id: str,
        *,
        unread_only: bool,
        severity: NotificationSeverity | None,
        notification_type: NotificationType | None,
        cursor: str | None,
        limit: int,
    ) -> list[Notification]:
        query: Document = {"organization_id": organization_id}
        if unread_only:
            query["is_read"] = False
        if severity:
            query["severity"] = severity.value
        if notification_type:
            query["type"] = notification_type.value
        if cursor:
            query["_id"] = {"$lt": cursor}
        try:
            documents = (
                await self._collection.find(query)
                .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
                .limit(limit)
                .to_list(length=limit)
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return [cast(Notification, _model(Notification, item)) for item in documents]

    async def unread_count(self, organization_id: str) -> int:
        try:
            return await self._collection.count_documents(
                {"organization_id": organization_id, "is_read": False}
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc

    async def mark_read(
        self, organization_id: str, notification_id: str, now: datetime
    ) -> Notification | None:
        try:
            document = await self._collection.find_one_and_update(
                {"_id": notification_id, "organization_id": organization_id},
                {"$set": {"is_read": True, "read_at": now}},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return _model(Notification, document)

    async def mark_all_read(self, organization_id: str, now: datetime) -> int:
        try:
            result = await self._collection.update_many(
                {"organization_id": organization_id, "is_read": False},
                {"$set": {"is_read": True, "read_at": now}},
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return result.modified_count

    async def cleanup_expired(self, organization_id: str, *, before: datetime, limit: int) -> int:
        try:
            documents = (
                await self._collection.find(
                    {
                        "organization_id": organization_id,
                        "$or": [
                            {"created_at": {"$lt": before}},
                            {"expires_at": {"$lt": before}},
                        ],
                    },
                    {"_id": 1},
                )
                .sort("created_at", ASCENDING)
                .limit(limit)
                .to_list(length=limit)
            )
            if not documents:
                return 0
            result = await self._collection.delete_many(
                {"_id": {"$in": [item["_id"] for item in documents]}}
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return result.deleted_count


class MongoServiceInstanceRepository:
    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def heartbeat(self, instance: ServiceInstance) -> ServiceInstance:
        try:
            await self._collection.replace_one(
                {"_id": instance.instance_id},
                _document(instance),
                upsert=True,
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return instance

    async def has_fresh(self, service_type: ServiceType, threshold: datetime) -> bool:
        try:
            document = await self._collection.find_one(
                {
                    "service_type": service_type.value,
                    "heartbeat_at": {"$gte": threshold},
                }
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return document is not None

    async def cleanup_expired(self, *, before: datetime, limit: int) -> int:
        try:
            documents = (
                await self._collection.find({"heartbeat_at": {"$lt": before}}, {"_id": 1})
                .sort("heartbeat_at", ASCENDING)
                .limit(limit)
                .to_list(length=limit)
            )
            if not documents:
                return 0
            result = await self._collection.delete_many(
                {"_id": {"$in": [item["_id"] for item in documents]}}
            )
        except PyMongoError as exc:
            raise RepositoryUnavailableError from exc
        return result.deleted_count
