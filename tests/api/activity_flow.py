"""The actions of an activity, sent through the API, shared by the timeline tests (RN49)."""

import datetime

from django.utils import timezone

from apps.activities.models import Activity, Submission
from tests.api.helpers import bearer, post_json
from tests.api.uploads import PDF, upload
from tests.factories import make_advising, make_course, make_student, make_teacher

BASE = "/api/atividades"


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def create_activity(client, teacher, student):
    response = post_json(
        client,
        BASE,
        {
            "student_id": student.user_id,
            "title": "Capítulo 2",
            "description": "Entregar o capítulo 2",
            "deadline": (timezone.localdate() + datetime.timedelta(days=7)).isoformat(),
        },
        headers=bearer(teacher.user),
    )
    assert response.status_code == 201
    return Activity.objects.get(pk=response.json()["id"])


def deliver(client, student, activity):
    return client.post(
        f"{BASE}/{activity.id}/entregas",
        data={"files": [upload("a.pdf", PDF)]},
        headers=bearer(student.user),
    )


def comment(client, user, activity, text="Um comentário."):
    return post_json(
        client, f"{BASE}/{activity.id}/comentarios", {"text": text}, headers=bearer(user)
    )


def change_status(client, student, activity, status):
    return client.patch(
        f"{BASE}/{activity.id}/status",
        data={"status": status},
        content_type="application/json",
        headers=bearer(student.user),
    )


def latest_submission_id(activity):
    """The id of the activity's latest delivery, the one the advisor has in sight, or 0 if none."""
    latest = Submission.objects.filter(activity=activity).order_by("-index").first()
    return latest.id if latest else 0


def accept(client, teacher, activity, submission_id=None):
    if submission_id is None:
        submission_id = latest_submission_id(activity)
    return post_json(
        client,
        f"{BASE}/{activity.id}/revisar",
        {"decision": "accepted", "submission_id": submission_id},
        headers=bearer(teacher.user),
    )


def refuse_without_files(client, teacher, activity, submission_id=None):
    if submission_id is None:
        submission_id = latest_submission_id(activity)
    return post_json(
        client,
        f"{BASE}/{activity.id}/revisar",
        {"decision": "rejected", "justification": "Refazer.", "submission_id": submission_id},
        headers=bearer(teacher.user),
    )


def refuse(client, teacher, activity, files=(), submission_id=None):
    if submission_id is None:
        submission_id = latest_submission_id(activity)
    return client.post(
        f"{BASE}/{activity.id}/recusar",
        data={"justification": "Refazer.", "submission_id": submission_id, "files": list(files)},
        headers=bearer(teacher.user),
    )


def end_advising(client, teacher, advising, reason="Concluímos o ciclo de orientação do TCC."):
    response = post_json(
        client,
        f"/api/orientacao/{advising.id}/encerrar",
        {"reason": reason},
        headers=bearer(teacher.user),
    )
    assert response.status_code == 200
    return response
