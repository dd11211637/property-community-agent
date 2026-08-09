import secrets
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from property_agent.platform.application.hashing import canonical_hash
from property_agent.repair.application.commands import (
    CreateReviewCommand,
    CreateWorkOrderCommand,
    ExecuteActionCommand,
    TimelineEntry,
    WorkOrderSearch,
)
from property_agent.repair.application.ports import (
    IdempotencyRecord,
    RepairUnitOfWork,
    RequestContext,
    UnitOfWorkFactory,
)
from property_agent.repair.domain.entities import WorkOrder
from property_agent.repair.domain.enums import (
    ActionCode,
    ProcessRecordType,
    RepairCategory,
    Role,
    Urgency,
    WorkOrderStatus,
)
from property_agent.repair.domain.errors import (
    BusinessError,
    forbidden,
    handover_required,
    idempotency_conflict,
    invalid_transition,
    not_found,
    validation_error,
    version_conflict,
)

CREATE_ROLES = (Role.RESIDENT, Role.CUSTOMER_SERVICE, Role.MANAGER)
ASSIGN_ROLES = (Role.CUSTOMER_SERVICE, Role.MANAGER)
READ_ROLES = (Role.RESIDENT, Role.CUSTOMER_SERVICE, Role.REPAIR_WORKER, Role.MANAGER)


__all__ = ["ASSIGN_ROLES", "CREATE_ROLES", "READ_ROLES", "WorkOrderService", "canonical_hash"]


class WorkOrderService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def create(
        self,
        command: CreateWorkOrderCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> WorkOrder:
        self._require_role(context, *CREATE_ROLES)
        self._require_idempotency_key(idempotency_key)
        self._validate_create(command)

        confirmed_parameters = asdict(command)
        confirmed_parameters.pop("confirmation_token")
        request_hash = canonical_hash(confirmed_parameters)

        if command.urgency == Urgency.HIGH_RISK:
            # PRD 6.1: a high-risk report never becomes an ordinary work order.
            # It creates a manual-handover ticket and notifies duty staff, and
            # the caller receives HANDOVER_REQUIRED carrying the ticket ID.
            ticket_id, notified = self._hand_over_high_risk(
                command,
                context,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            raise handover_required(handover_ticket_id=ticket_id, notified_staff=notified)

        operation = "CREATE_WORK_ORDER"
        with self._unit_of_work_factory() as uow:
            replay = self._idempotent_replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay

            uow.house_access.ensure_access(
                actor_id=context.actor_id,
                community_id=context.community_id,
                house_id=command.house_id,
                request_id=context.request_id,
            )
            uow.attachments.ensure_usable(
                attachment_ids=command.attachment_ids,
                actor_id=context.actor_id,
                community_id=context.community_id,
                request_id=context.request_id,
            )
            uow.confirmations.consume(
                token=command.confirmation_token,
                actor_id=context.actor_id,
                action=operation,
                parameter_hash=request_hash,
                request_id=context.request_id,
            )

            now = datetime.now(UTC)
            work_order = WorkOrder(
                id=uuid4(),
                community_id=context.community_id,
                business_no=self._new_business_no(now),
                house_id=command.house_id,
                reporter_id=context.actor_id,
                category=command.category,
                location=command.location.strip(),
                description=command.description.strip(),
                urgency=command.urgency,
                create_idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
            uow.work_orders.add(work_order)
            self._add_status_log(
                uow,
                work_order,
                context,
                action=ActionCode.CREATE,
                from_status=None,
                reason=None,
                now=now,
            )
            uow.idempotency.add(
                IdempotencyRecord(
                    actor_id=context.actor_id,
                    operation=operation,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=work_order.id,
                    response_snapshot=self._snapshot(work_order),
                )
            )
            self._audit(
                uow,
                work_order,
                context,
                action=ActionCode.CREATE,
                parameters={
                    "house_id": str(command.house_id),
                    "category": command.category.value,
                    "urgency": command.urgency.value,
                },
                now=now,
            )
            uow.commit()
            return work_order

    def search(self, search: WorkOrderSearch, context: RequestContext) -> list[WorkOrder]:
        self._require_role(context, *READ_ROLES)
        if search.limit < 1 or search.limit > 100 or search.offset < 0:
            raise validation_error("Pagination must use offset >= 0 and limit between 1 and 100.")
        if (
            search.house_id is not None
            and context.has_any_role(Role.RESIDENT)
            and not context.has_any_role(Role.CUSTOMER_SERVICE, Role.MANAGER)
        ):
            if search.house_id not in context.house_ids:
                raise forbidden()
        with self._unit_of_work_factory() as uow:
            return list(uow.work_orders.list(context.community_id, search, context))

    def get(self, work_order_id: UUID, context: RequestContext) -> WorkOrder:
        with self._unit_of_work_factory() as uow:
            return self._get_authorized(uow, work_order_id, context)

    def timeline(self, work_order_id: UUID, context: RequestContext) -> list[TimelineEntry]:
        with self._unit_of_work_factory() as uow:
            work_order = self._get_authorized(uow, work_order_id, context)
            return list(uow.work_orders.timeline(work_order.id, context.community_id))

    def execute_action(
        self,
        work_order_id: UUID,
        command: ExecuteActionCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> WorkOrder:
        self._require_idempotency_key(idempotency_key)
        operation = f"WORK_ORDER_{command.action.value}"
        request_hash = canonical_hash({"work_order_id": work_order_id, **asdict(command)})
        with self._unit_of_work_factory() as uow:
            replay = self._idempotent_replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay

            work_order = self._get_authorized(uow, work_order_id, context)
            if work_order.version != command.expected_version:
                raise version_conflict(work_order.version)
            now = datetime.now(UTC)
            from_status = work_order.status
            reason = self._apply_action(uow, work_order, command, context, now)
            uow.idempotency.add(
                IdempotencyRecord(
                    actor_id=context.actor_id,
                    operation=operation,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=work_order.id,
                    response_snapshot=self._snapshot(work_order),
                )
            )
            if command.action != ActionCode.RECORD_PROGRESS:
                self._add_status_log(
                    uow,
                    work_order,
                    context,
                    action=self._effective_action(command.action, from_status),
                    from_status=from_status.value,
                    reason=reason,
                    now=now,
                )
            uow.work_orders.save(work_order)
            self._audit(
                uow,
                work_order,
                context,
                action=command.action,
                parameters={"reason": reason, "record_type": command.record_type},
                now=now,
            )
            self._enqueue_action_message(uow, work_order, command.action, context, now)
            uow.commit()
            return work_order

    def create_review(
        self,
        work_order_id: UUID,
        command: CreateReviewCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> WorkOrder:
        self._require_idempotency_key(idempotency_key)
        if command.rating < 1 or command.rating > 5:
            raise validation_error("Rating must be between 1 and 5.")
        operation = "WORK_ORDER_CREATE_REVIEW"
        request_hash = canonical_hash({"work_order_id": work_order_id, **asdict(command)})
        with self._unit_of_work_factory() as uow:
            replay = self._idempotent_replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            work_order = self._get_authorized(uow, work_order_id, context)
            if work_order.status != WorkOrderStatus.CLOSED:
                raise validation_error("Only a closed work order can be reviewed.")
            if (
                not context.has_any_role(Role.RESIDENT)
                or work_order.house_id not in context.house_ids
            ):
                raise forbidden()
            if work_order.has_review:
                raise BusinessError(
                    "REVIEW_ALREADY_EXISTS",
                    "This work order already has a review.",
                    409,
                )
            now = datetime.now(UTC)
            uow.work_orders.add_review(
                work_order_id=work_order.id,
                community_id=context.community_id,
                reviewer_id=context.actor_id,
                rating=command.rating,
                comment=self._optional_text(command.comment),
                created_at=now,
            )
            work_order.has_review = True
            uow.idempotency.add(
                IdempotencyRecord(
                    actor_id=context.actor_id,
                    operation=operation,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=work_order.id,
                    response_snapshot=self._snapshot(work_order),
                )
            )
            self._audit(
                uow,
                work_order,
                context,
                action=ActionCode.CREATE_REVIEW,
                parameters={"rating": command.rating},
                now=now,
            )
            uow.commit()
            return work_order

    def available_actions(self, work_order: WorkOrder, context: RequestContext) -> list[ActionCode]:
        status = work_order.status
        if status == WorkOrderStatus.PENDING_ASSIGNMENT and context.has_any_role(*ASSIGN_ROLES):
            return [ActionCode.ASSIGN]
        if (
            status == WorkOrderStatus.PENDING_ACCEPTANCE
            and context.has_any_role(Role.REPAIR_WORKER)
            and work_order.assignee_id == context.actor_id
        ):
            return [ActionCode.ACCEPT, ActionCode.REJECT]
        if (
            status in {WorkOrderStatus.PROCESSING, WorkOrderStatus.REWORKING}
            and context.has_any_role(Role.REPAIR_WORKER)
            and work_order.assignee_id == context.actor_id
        ):
            completion = (
                ActionCode.SUBMIT_COMPLETION
                if status == WorkOrderStatus.PROCESSING
                else ActionCode.SUBMIT_REWORK_COMPLETION
            )
            return [ActionCode.RECORD_PROGRESS, completion]
        if status == WorkOrderStatus.PENDING_VERIFICATION and (
            context.has_any_role(Role.MANAGER)
            or (context.has_any_role(Role.RESIDENT) and work_order.house_id in context.house_ids)
        ):
            return [ActionCode.VERIFY_PASS, ActionCode.REQUEST_REWORK]
        if (
            status == WorkOrderStatus.CLOSED
            and not work_order.has_review
            and context.has_any_role(Role.RESIDENT)
            and work_order.house_id in context.house_ids
        ):
            return [ActionCode.CREATE_REVIEW]
        return []

    def _apply_action(
        self,
        uow: RepairUnitOfWork,
        work_order: WorkOrder,
        command: ExecuteActionCommand,
        context: RequestContext,
        now: datetime,
    ) -> str | None:
        action = command.action
        if action == ActionCode.ASSIGN:
            return self._assign(uow, work_order, command, context, now)

        if action in {
            ActionCode.ACCEPT,
            ActionCode.REJECT,
            ActionCode.RECORD_PROGRESS,
            ActionCode.SUBMIT_COMPLETION,
            ActionCode.SUBMIT_REWORK_COMPLETION,
        }:
            self._require_assignee(work_order, context)

        if action == ActionCode.REJECT:
            reason = self._required_text(command.reason, "A rejection reason is required.")
            work_order.transition(action, now=now)
            return reason

        if action == ActionCode.ACCEPT:
            work_order.transition(action, now=now)
            return None

        if action == ActionCode.RECORD_PROGRESS:
            return self._record_progress(uow, work_order, command, context, now)

        if action in {ActionCode.SUBMIT_COMPLETION, ActionCode.SUBMIT_REWORK_COMPLETION}:
            return self._submit_completion(uow, work_order, command, context, now)

        if action in {ActionCode.VERIFY_PASS, ActionCode.REQUEST_REWORK}:
            return self._verify(work_order, command, context, now)

        raise validation_error(f"Unsupported action: {action.value}.")

    def _assign(
        self,
        uow: RepairUnitOfWork,
        work_order: WorkOrder,
        command: ExecuteActionCommand,
        context: RequestContext,
        now: datetime,
    ) -> None:
        self._require_role(context, *ASSIGN_ROLES)
        if command.assignee_id is None:
            raise validation_error("assignee_id is required.")
        uow.staff_directory.ensure_repair_worker(
            user_id=command.assignee_id,
            community_id=context.community_id,
            request_id=context.request_id,
        )
        work_order.assignee_id = command.assignee_id
        work_order.transition(command.action, now=now)

    def _record_progress(
        self,
        uow: RepairUnitOfWork,
        work_order: WorkOrder,
        command: ExecuteActionCommand,
        context: RequestContext,
        now: datetime,
    ) -> str:
        self._require_state_action(work_order, command.action)
        if command.record_type not in {
            ProcessRecordType.APPOINTMENT,
            ProcessRecordType.ARRIVAL,
            ProcessRecordType.PROGRESS,
            ProcessRecordType.BLOCKED,
        }:
            raise validation_error("record_type is invalid for a progress record.")
        note = self._required_text(command.note, "A progress note is required.")
        uow.attachments.ensure_usable(
            attachment_ids=command.attachment_ids,
            actor_id=context.actor_id,
            community_id=context.community_id,
            request_id=context.request_id,
        )
        uow.work_orders.add_process_record(
            work_order_id=work_order.id,
            community_id=context.community_id,
            record_type=command.record_type,
            note=note,
            operator_id=context.actor_id,
            appointment_at=command.appointment_at,
            attachment_ids=command.attachment_ids,
            created_at=now,
        )
        work_order.touch(now=now)
        return note

    def _submit_completion(
        self,
        uow: RepairUnitOfWork,
        work_order: WorkOrder,
        command: ExecuteActionCommand,
        context: RequestContext,
        now: datetime,
    ) -> str:
        self._require_state_action(work_order, command.action)
        note = self._required_text(command.note, "A completion note is required.")
        uow.attachments.ensure_usable(
            attachment_ids=command.attachment_ids,
            actor_id=context.actor_id,
            community_id=context.community_id,
            request_id=context.request_id,
        )
        uow.work_orders.add_process_record(
            work_order_id=work_order.id,
            community_id=context.community_id,
            record_type=ProcessRecordType.COMPLETION,
            note=note,
            operator_id=context.actor_id,
            appointment_at=None,
            attachment_ids=command.attachment_ids,
            created_at=now,
        )
        work_order.transition(command.action, now=now)
        return note

    def _verify(
        self,
        work_order: WorkOrder,
        command: ExecuteActionCommand,
        context: RequestContext,
        now: datetime,
    ) -> str | None:
        self._require_verifier(work_order, context)
        reason = None
        if command.action == ActionCode.REQUEST_REWORK:
            reason = self._required_text(command.reason, "A rework reason is required.")
        work_order.transition(command.action, now=now)
        return reason

    def _get_authorized(
        self, uow: RepairUnitOfWork, work_order_id: UUID, context: RequestContext
    ) -> WorkOrder:
        self._require_role(context, *READ_ROLES)
        work_order = uow.work_orders.get(work_order_id, context.community_id)
        if work_order is None:
            raise not_found()
        if (
            context.has_any_role(Role.RESIDENT)
            and not context.has_any_role(Role.CUSTOMER_SERVICE, Role.MANAGER)
            and work_order.house_id not in context.house_ids
        ):
            raise not_found()
        if (
            context.has_any_role(Role.REPAIR_WORKER)
            and not context.has_any_role(Role.CUSTOMER_SERVICE, Role.MANAGER)
            and work_order.assignee_id != context.actor_id
        ):
            raise not_found()
        return work_order

    # ── High-risk manual handover (PRD 6.1) ────────────────────────

    def _hand_over_high_risk(
        self,
        command: CreateWorkOrderCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[UUID, int]:
        """Create a manual-handover ticket for a high-risk report.

        Performs the same authorisation, attachment and confirmation checks as
        a normal creation, then persists the ticket, notifies every duty staff
        member and writes the audit trail — all in one transaction. Nothing is
        returned to the caller until the commit succeeds, so a failure never
        produces a fake ticket number (PRD 6.1 "接口失败不生成虚假单号").

        Returns the ticket ID and the number of notified staff members.
        """
        operation = "CREATE_WORK_ORDER_HANDOVER"
        with self._unit_of_work_factory() as uow:
            existing = uow.idempotency.get(context.actor_id, operation, idempotency_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise idempotency_conflict()
                snapshot = existing.response_snapshot
                return existing.resource_id, int(snapshot.get("notified_staff", 0))

            uow.house_access.ensure_access(
                actor_id=context.actor_id,
                community_id=context.community_id,
                house_id=command.house_id,
                request_id=context.request_id,
            )
            uow.attachments.ensure_usable(
                attachment_ids=command.attachment_ids,
                actor_id=context.actor_id,
                community_id=context.community_id,
                request_id=context.request_id,
            )
            uow.confirmations.consume(
                token=command.confirmation_token,
                actor_id=context.actor_id,
                action=operation,
                parameter_hash=request_hash,
                request_id=context.request_id,
            )

            now = datetime.now(UTC)
            summary = f"高风险报修待人工核实：{command.category.value} / {command.location.strip()}"
            ticket_id = uow.handover.create(
                community_id=context.community_id,
                requester_id=context.actor_id,
                queue="CUSTOMER_SERVICE",
                reason="HIGH_RISK",
                summary=summary,
                payload={
                    "house_id": str(command.house_id),
                    "category": command.category.value,
                    "urgency": command.urgency.value,
                    "location": command.location.strip(),
                    "description": command.description.strip(),
                    "attachment_ids": [str(item) for item in command.attachment_ids],
                },
                request_id=context.request_id,
                created_at=now,
            )

            duty_staff = uow.staff_directory.list_duty_staff(
                community_id=context.community_id,
                request_id=context.request_id,
            )
            for receiver_id in duty_staff:
                uow.messages.enqueue(
                    community_id=context.community_id,
                    receiver_id=receiver_id,
                    event_type="HIGH_RISK_HANDOVER",
                    resource_id=ticket_id,
                    request_id=context.request_id,
                    created_at=now,
                )

            uow.idempotency.add(
                IdempotencyRecord(
                    actor_id=context.actor_id,
                    operation=operation,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=ticket_id,
                    response_snapshot={
                        "handover_ticket_id": str(ticket_id),
                        "notified_staff": len(duty_staff),
                    },
                )
            )
            uow.audit.add(
                community_id=context.community_id,
                actor_id=context.actor_id,
                action="HIGH_RISK_HANDOVER",
                resource_type="HANDOVER_TICKET",
                resource_id=ticket_id,
                parameter_summary={
                    "house_id": str(command.house_id),
                    "category": command.category.value,
                    "urgency": command.urgency.value,
                    "notified_staff": len(duty_staff),
                },
                request_id=context.request_id,
                created_at=now,
            )
            uow.commit()
            return ticket_id, len(duty_staff)

    def _idempotent_replay(
        self,
        uow: RepairUnitOfWork,
        context: RequestContext,
        operation: str,
        key: str,
        request_hash: str,
    ) -> WorkOrder | None:
        existing = uow.idempotency.get(context.actor_id, operation, key)
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise idempotency_conflict()
        return self._from_snapshot(existing.response_snapshot)

    @staticmethod
    def _validate_create(command: CreateWorkOrderCommand) -> None:
        if not command.location.strip():
            raise validation_error("location is required.")
        if len(command.location.strip()) > 128:
            raise validation_error("location must not exceed 128 characters.")
        if not command.description.strip():
            raise validation_error("description is required.")
        if not command.confirmation_token.strip():
            raise BusinessError(
                "CONFIRMATION_REQUIRED",
                "A confirmation token is required.",
                422,
            )

    @staticmethod
    def _require_role(context: RequestContext, *roles: Role) -> None:
        if not context.has_any_role(*roles):
            raise forbidden()

    @staticmethod
    def _require_assignee(work_order: WorkOrder, context: RequestContext) -> None:
        if (
            not context.has_any_role(Role.REPAIR_WORKER)
            or work_order.assignee_id != context.actor_id
        ):
            raise forbidden()

    @staticmethod
    def _require_verifier(work_order: WorkOrder, context: RequestContext) -> None:
        if context.has_any_role(Role.MANAGER):
            return
        if context.has_any_role(Role.RESIDENT) and work_order.house_id in context.house_ids:
            return
        raise forbidden()

    @staticmethod
    def _require_idempotency_key(key: str) -> None:
        if not key or not key.strip() or len(key) > 128:
            raise validation_error(
                "Idempotency-Key is required and must not exceed 128 characters."
            )

    @staticmethod
    def _require_state_action(work_order: WorkOrder, action: ActionCode) -> None:
        if action not in work_order.state_actions():
            raise invalid_transition(
                work_order.status.value,
                action.value,
                [item.value for item in work_order.state_actions()],
            )

    @staticmethod
    def _required_text(value: str | None, message: str) -> str:
        if value is None or not value.strip():
            raise validation_error(message)
        return value.strip()

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @staticmethod
    def _new_business_no(now: datetime) -> str:
        return f"WX-{now:%Y%m%d}-{secrets.token_hex(4).upper()}"

    @staticmethod
    def _snapshot(work_order: WorkOrder) -> dict[str, Any]:
        return {
            "id": str(work_order.id),
            "community_id": str(work_order.community_id),
            "business_no": work_order.business_no,
            "house_id": str(work_order.house_id),
            "reporter_id": str(work_order.reporter_id),
            "category": work_order.category.value,
            "location": work_order.location,
            "description": work_order.description,
            "urgency": work_order.urgency.value,
            "create_idempotency_key": work_order.create_idempotency_key,
            "status": work_order.status.value,
            "assignee_id": str(work_order.assignee_id) if work_order.assignee_id else None,
            "version": work_order.version,
            "created_at": work_order.created_at.isoformat(),
            "updated_at": work_order.updated_at.isoformat(),
            "closed_at": work_order.closed_at.isoformat() if work_order.closed_at else None,
            "has_review": work_order.has_review,
        }

    @staticmethod
    def _from_snapshot(snapshot: dict[str, Any]) -> WorkOrder:
        return WorkOrder(
            id=UUID(snapshot["id"]),
            community_id=UUID(snapshot["community_id"]),
            business_no=snapshot["business_no"],
            house_id=UUID(snapshot["house_id"]),
            reporter_id=UUID(snapshot["reporter_id"]),
            category=RepairCategory(snapshot["category"]),
            location=snapshot["location"],
            description=snapshot["description"],
            urgency=Urgency(snapshot["urgency"]),
            create_idempotency_key=snapshot["create_idempotency_key"],
            status=WorkOrderStatus(snapshot["status"]),
            assignee_id=UUID(snapshot["assignee_id"]) if snapshot["assignee_id"] else None,
            version=snapshot["version"],
            created_at=datetime.fromisoformat(snapshot["created_at"]),
            updated_at=datetime.fromisoformat(snapshot["updated_at"]),
            closed_at=(
                datetime.fromisoformat(snapshot["closed_at"]) if snapshot["closed_at"] else None
            ),
            has_review=snapshot["has_review"],
        )

    @staticmethod
    def _effective_action(action: ActionCode, from_status: WorkOrderStatus) -> ActionCode:
        if action == ActionCode.SUBMIT_COMPLETION and from_status == WorkOrderStatus.REWORKING:
            return ActionCode.SUBMIT_REWORK_COMPLETION
        return action

    @staticmethod
    def _operator_role(context: RequestContext) -> Role:
        for role in (
            Role.MANAGER,
            Role.CUSTOMER_SERVICE,
            Role.REPAIR_WORKER,
            Role.RESIDENT,
        ):
            if role in context.roles:
                return role
        raise forbidden()

    def _add_status_log(
        self,
        uow: RepairUnitOfWork,
        work_order: WorkOrder,
        context: RequestContext,
        *,
        action: ActionCode,
        from_status: str | None,
        reason: str | None,
        now: datetime,
    ) -> None:
        uow.work_orders.add_status_log(
            work_order_id=work_order.id,
            community_id=context.community_id,
            from_status=from_status,
            action=action,
            to_status=work_order.status.value,
            operator_id=context.actor_id,
            operator_role=self._operator_role(context),
            reason=reason,
            request_id=context.request_id,
            created_at=now,
        )

    def _audit(
        self,
        uow: RepairUnitOfWork,
        work_order: WorkOrder,
        context: RequestContext,
        *,
        action: ActionCode,
        parameters: dict[str, Any],
        now: datetime,
    ) -> None:
        uow.audit.add(
            community_id=context.community_id,
            actor_id=context.actor_id,
            action=action.value,
            resource_type="WORK_ORDER",
            resource_id=work_order.id,
            parameter_summary=parameters,
            request_id=context.request_id,
            created_at=now,
        )

    @staticmethod
    def _enqueue_action_message(
        uow: RepairUnitOfWork,
        work_order: WorkOrder,
        action: ActionCode,
        context: RequestContext,
        now: datetime,
    ) -> None:
        receiver_id: UUID | None = None
        if action == ActionCode.ASSIGN:
            receiver_id = work_order.assignee_id
        elif action in {ActionCode.SUBMIT_COMPLETION, ActionCode.SUBMIT_REWORK_COMPLETION}:
            receiver_id = work_order.reporter_id
        elif action == ActionCode.REQUEST_REWORK:
            receiver_id = work_order.assignee_id
        if receiver_id is not None:
            uow.messages.enqueue(
                community_id=context.community_id,
                receiver_id=receiver_id,
                event_type=action.value,
                resource_id=work_order.id,
                request_id=context.request_id,
                created_at=now,
            )
