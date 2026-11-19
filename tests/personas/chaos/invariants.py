"""Business rules the simulated accounts must never break, read straight from the database.

Each check returns the violations it found as readable messages; none means the rule holds. The
checks that compare with an earlier moment keep what they saw in an `InvariantMemory`.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import NamedTuple

from django.db.models import Count, F, Q

from apps.accounts.demo_seed import DEMO_DOMAIN
from apps.accounts.models import User
from apps.activities.models import (
    Activity,
    ActivityStatus,
    Comment,
    Review,
    ReviewDecision,
    StatusChange,
    Submission,
)
from apps.advising.models import Advising, AdvisingRequest, AdvisingRequestStatus, AdvisingStatus
from apps.advising.services import SYSTEM_END_REASON
from apps.meetings.models import Meeting, MeetingStatus
from apps.notifications.models import ACTIVITY_REFERENCE, Notification, NotificationType

SIMULATED_STUDENT_PREFIX = "aluno.simulado."
OPEN_ACTIVITY_STATUSES = (
    ActivityStatus.PENDING,
    ActivityStatus.IN_PROGRESS,
    ActivityStatus.IN_REVIEW,
)
STUDENT_CONTROLLED_STATUSES = (ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS)
MAX_JUSTIFICATION_LENGTH = 500


def is_simulated(prefix: str = "") -> Q:
    return Q(**{f"{prefix}email__iendswith": f"@{DEMO_DOMAIN}"})


@dataclass
class InvariantMemory:
    answered_requests: dict[int, tuple] = field(default_factory=dict)
    ended_advisings: dict[int, tuple] = field(default_factory=dict)


def all_violations(memory: InvariantMemory, real_user_ids: set[int]) -> list[str]:
    return [
        *one_active_advising_and_one_pending_request_per_student(),
        *answered_requests_never_change(memory),
        *accepted_requests_match_their_advising(),
        *ended_advisings_stay_ended_and_frozen(memory),
        *ended_advisings_leave_nothing_open(),
        *deactivated_accounts_keep_no_bond(),
        *simulated_accounts_stay_in_their_lane(real_user_ids),
        *activity_status_follows_the_flow(),
        *reviews_come_from_the_advising_teacher(),
    ]


def one_active_advising_and_one_pending_request_per_student() -> list[str]:
    """RN04 and RN05."""
    violations = []
    many_active = (
        Advising.objects.filter(status=AdvisingStatus.ACTIVE)
        .values("student_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for row in many_active:
        violations.append(f"RN04: student {row['student_id']} has {row['total']} active advisings")
    many_pending = (
        AdvisingRequest.objects.filter(status=AdvisingRequestStatus.PENDING)
        .values("student_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for row in many_pending:
        violations.append(f"RN05: student {row['student_id']} has {row['total']} pending requests")
    pending_with_advising = AdvisingRequest.objects.valid_pending().filter(
        student__advisings__status=AdvisingStatus.ACTIVE
    )
    for request in pending_with_advising:
        violations.append(
            f"RN05: student {request.student_id} has a pending request {request.pk} "
            "and an active advising"
        )
    return violations


def answered_requests_never_change(memory: InvariantMemory) -> list[str]:
    """RN06: a request once accepted or rejected is never altered."""
    violations = []
    answered = AdvisingRequest.objects.filter(
        status__in=(AdvisingRequestStatus.ACCEPTED, AdvisingRequestStatus.REJECTED)
    )
    for request in answered:
        snapshot = (
            request.status,
            request.student_id,
            request.teacher_id,
            request.requested_at,
            request.responded_at,
        )
        known = memory.answered_requests.setdefault(request.pk, snapshot)
        if known[:3] != snapshot[:3] or known[4] != snapshot[4]:
            violations.append(f"RN06: answered request {request.pk} changed: {known} -> {snapshot}")
    for request_id in set(memory.answered_requests) - {request.pk for request in answered}:
        violations.append(f"RN06: answered request {request_id} is no longer answered")
    return violations


def accepted_requests_match_their_advising() -> list[str]:
    violations = []
    for request in AdvisingRequest.objects.filter(
        status=AdvisingRequestStatus.ACCEPTED, advising__isnull=True
    ):
        violations.append(f"RN04: accepted request {request.pk} started no advising")
    for advising in Advising.objects.filter(request__isnull=False).exclude(
        request__status=AdvisingRequestStatus.ACCEPTED
    ):
        violations.append(f"RN06: advising {advising.pk} came from a request that is not accepted")
    return violations


def _content_of(advising: Advising) -> tuple:
    topic = getattr(advising, "thesis_topic", None)
    return (
        Activity.objects.filter(advising=advising).count(),
        Submission.objects.filter(activity__advising=advising).count(),
        Review.objects.filter(activity__advising=advising).count(),
        StatusChange.objects.filter(activity__advising=advising).count(),
        Comment.objects.filter(activity__advising=advising).count(),
        Meeting.objects.filter(advising=advising).count(),
        topic.description if topic else None,
    )


def ended_advisings_stay_ended_and_frozen(memory: InvariantMemory) -> list[str]:
    """RN07, RN12 and RN30: an ended advising is never reopened, and nothing is added to it."""
    violations = []
    ended = Advising.objects.filter(status=AdvisingStatus.ENDED).select_related("thesis_topic")
    for advising in ended:
        snapshot = (advising.ended_at, advising.end_reason, _content_of(advising))
        known = memory.ended_advisings.setdefault(advising.pk, snapshot)
        if known != snapshot:
            violations.append(
                f"advising {advising.pk} changed after it ended: {known} -> {snapshot}"
            )
    for advising_id in set(memory.ended_advisings) - {advising.pk for advising in ended}:
        violations.append(f"advising {advising_id} was reopened after it ended")
    return violations


def ended_advisings_leave_nothing_open() -> list[str]:
    """RN07 archives the open activities and RN34 cancels the future meetings of an ended advising."""
    violations = []
    open_activities = Activity.objects.filter(
        advising__status=AdvisingStatus.ENDED,
        deleted_at__isnull=True,
        status__in=OPEN_ACTIVITY_STATUSES,
    )
    for activity in open_activities:
        violations.append(
            f"RN07: activity {activity.pk} is {activity.status} in the ended advising "
            f"{activity.advising_id}"
        )
    open_meetings = Meeting.objects.filter(
        advising__status=AdvisingStatus.ENDED,
        status__in=(MeetingStatus.PENDING, MeetingStatus.ACCEPTED),
        held_at__isnull=True,
    ).filter(scheduled_at__gt=F("advising__ended_at"))
    for meeting in open_meetings:
        violations.append(
            f"RN34: meeting {meeting.pk} is {meeting.status} in the ended advising "
            f"{meeting.advising_id}"
        )
    archived_outside_end = Activity.objects.filter(
        status=ActivityStatus.ARCHIVED, advising__status=AdvisingStatus.ACTIVE
    )
    for activity in archived_outside_end:
        violations.append(f"RN07: activity {activity.pk} is archived in an active advising")
    return violations


def deactivated_accounts_keep_no_bond() -> list[str]:
    """RN19: a deactivated account has no active advising and no pending request."""
    violations = []
    inactive = Q(student__user__is_active=False) | Q(teacher__user__is_active=False)
    for advising in Advising.objects.filter(inactive, status=AdvisingStatus.ACTIVE):
        violations.append(f"RN19: advising {advising.pk} is active with a deactivated account")
    for request in AdvisingRequest.objects.filter(inactive, status=AdvisingRequestStatus.PENDING):
        violations.append(f"RN19: request {request.pk} is pending with a deactivated account")
    return violations


def simulated_accounts_stay_in_their_lane(real_user_ids: set[int]) -> list[str]:
    """ADR 0022: the simulated accounts are all in the reserved domain, are never deactivated,
    never end an advising, never talk to each other and never act with a teacher that is not their
    own."""
    violations = []
    for user in User.objects.filter(is_simulated(), is_active=False):
        violations.append(f"simulated account {user.email} was deactivated")
    for user in User.objects.exclude(is_simulated()).exclude(pk__in=real_user_ids):
        violations.append(f"account {user.email} was created outside the simulation domain")
    for user in User.objects.filter(full_name__icontains="simulado").exclude(is_simulated()):
        violations.append(f"real account {user.email} is labelled as simulated")
    for user in User.objects.filter(is_simulated()).exclude(full_name__icontains="simulado"):
        violations.append(f"simulated account {user.email} is not labelled as simulated")
    both_simulated = is_simulated("student__user__") & is_simulated("teacher__user__")
    for model in (Advising, AdvisingRequest):
        for row in model.objects.filter(both_simulated):
            violations.append(f"{model.__name__} {row.pk} links two simulated accounts")
    for advising in Advising.objects.filter(
        is_simulated("teacher__user__"), status=AdvisingStatus.ENDED
    ).exclude(end_reason=SYSTEM_END_REASON):
        violations.append(f"the simulated teacher ended the advising {advising.pk}")
    system_ended = Advising.objects.filter(end_reason=SYSTEM_END_REASON).exclude(
        Q(student__user__is_active=False) | Q(teacher__user__is_active=False)
    )
    for advising in system_ended:
        violations.append(
            f"advising {advising.pk} was ended by the system with both accounts active"
        )
    for advising in Advising.objects.filter(
        is_simulated("student__user__"), student__user__email__istartswith=SIMULATED_STUDENT_PREFIX
    ).select_related("student__user"):
        owner_id = advising.student.user.email.partition("@")[0].rsplit(".", 1)[1]
        if str(advising.teacher.user_id) != owner_id:
            violations.append(
                f"{advising.student.user.email} has the advising {advising.pk} "
                f"with the teacher {advising.teacher.user_id}"
            )
    for meeting in Meeting.objects.filter(is_simulated("author__")):
        violations.append(f"meeting {meeting.pk} was proposed by a simulated account")
    for comment in Comment.objects.filter(is_simulated("author__")):
        violations.append(f"comment {comment.pk} was written by a simulated account")
    return violations


class Event(NamedTuple):
    index: int
    kind: str
    status: str = ""
    justification: str = ""


def _events_by_activity() -> dict[int, list[Event]]:
    events: dict[int, list[Event]] = defaultdict(list)
    for activity_id, index in Submission.objects.values_list("activity_id", "index"):
        events[activity_id].append(Event(index, "submission"))
    for activity_id, index, status in StatusChange.objects.values_list(
        "activity_id", "index", "status"
    ):
        events[activity_id].append(Event(index, "status_change", status))
    for activity_id, index, decision, justification in Review.objects.values_list(
        "activity_id", "index", "decision", "justification"
    ):
        events[activity_id].append(Event(index, "review", decision, justification))
    for activity_id, index in Comment.objects.values_list("activity_id", "index"):
        events[activity_id].append(Event(index, "comment"))
    return events


def replay_activity(events: list[Event]) -> tuple[str, list[str]]:
    """The status an activity reaches by its timeline, and every step the flow did not allow."""
    status = ActivityStatus.PENDING
    delivered = False
    problems = []
    for event in sorted(events):
        step = f"{event.kind}#{event.index} while {status}"
        if event.kind == "submission":
            delivered = True
            if status not in STUDENT_CONTROLLED_STATUSES:
                problems.append(f"RN16: delivery {step}")
        elif event.kind == "status_change":
            if status not in STUDENT_CONTROLLED_STATUSES:
                problems.append(f"RN15: the student changed the status {step}")
            if event.status == ActivityStatus.IN_REVIEW and not delivered:
                problems.append(f"RF28: sent for review without a delivery ({step})")
            status = event.status
        elif event.kind == "review":
            if status != ActivityStatus.IN_REVIEW:
                problems.append(f"RN21: review {step}")
            if event.status == ReviewDecision.ACCEPTED:
                if event.justification:
                    problems.append(f"RN40: acceptance with justification ({step})")
                status = ActivityStatus.COMPLETED
            else:
                if not 1 <= len(event.justification) <= MAX_JUSTIFICATION_LENGTH:
                    problems.append(f"RN40: refusal without a valid justification ({step})")
                status = ActivityStatus.PENDING
        elif status == ActivityStatus.COMPLETED:
            problems.append(f"RN15: comment on a completed activity ({step})")
    return status, problems


def activity_status_follows_the_flow() -> list[str]:
    """RN15, RN16, RN21 and RN40: replaying the timeline of every activity gives its status."""
    violations = []
    events = _events_by_activity()
    activities = Activity.objects.values_list("pk", "status", "advising__status")
    for activity_id, persisted, advising_status in activities:
        replayed, problems = replay_activity(events[activity_id])
        violations.extend(f"activity {activity_id}: {problem}" for problem in problems)
        archived_by_the_end = (
            persisted == ActivityStatus.ARCHIVED
            and advising_status == AdvisingStatus.ENDED
            and replayed in OPEN_ACTIVITY_STATUSES
        )
        if persisted != replayed and not archived_by_the_end:
            violations.append(
                f"activity {activity_id} is {persisted} but its timeline leads to {replayed}"
            )
    return violations


def reviews_come_from_the_advising_teacher() -> list[str]:
    """RN21: each review notifies the student with the name of the teacher of that advising."""
    violations = []
    reviews = Counter(Review.objects.values_list("activity_id", flat=True))
    teacher_names = dict(
        Activity.objects.filter(pk__in=reviews).values_list(
            "pk", "advising__teacher__user__full_name"
        )
    )
    notified = defaultdict(list)
    results = Notification.objects.filter(
        type=NotificationType.ACTIVITY_REVIEW_RESULT, reference_type=ACTIVITY_REFERENCE
    )
    for activity_id, payload in results.values_list("reference_id", "payload"):
        notified[activity_id].append(payload["teacher_name"])
    for activity_id, total in reviews.items():
        if notified[activity_id] != [teacher_names[activity_id]] * total:
            violations.append(
                f"RN21: activity {activity_id} has {total} reviews but its notifications name "
                f"{notified[activity_id]} and its teacher is {teacher_names[activity_id]}"
            )
    return violations
