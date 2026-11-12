"""Ending an advising racing with the creation of a meeting or an activity (ADR 0004, #55).

Real threads and transactions against PostgreSQL. Whichever side gets the advising lock first, the
result must be coherent: the creation is refused (404 for an activity, 409 for a meeting) and
leaves nothing, or it succeeds and the ending cancels or archives what it created. No meeting or
activity is ever left open in an ended advising.
"""

import datetime
import threading

import pytest
from django.db import connection, transaction
from django.test import Client
from django.utils import timezone

from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import Advising, AdvisingStatus
from apps.meetings.models import Meeting, MeetingStatus
from tests.api.helpers import bearer, post_json, wait_for_a_request_blocked_on_a_lock
from tests.api.journeys.helpers import at, end_advising
from tests.factories import make_advising, make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db(transaction=True)

RACES = 12
OPEN_ACTIVITY = (ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW)
OPEN_MEETING = (MeetingStatus.PENDING, MeetingStatus.ACCEPTED)


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def meeting_body():
    date, time = at(days=3)
    return {
        "date": date,
        "time": time,
        "end_time": "23:59:59",
        "description": "Revisar o capítulo 2 do TCC",
    }


def activity_body(student):
    return {
        "student_id": student.user_id,
        "title": "Capítulo 2",
        "description": "Entregar o capítulo 2 do TCC",
        "deadline": (timezone.localdate() + datetime.timedelta(days=10)).isoformat(),
    }


def race(first, second):
    """Runs two callables at the same instant, each in its own thread and database connection."""
    barrier = threading.Barrier(2)
    results, failures = {}, []

    def run(name, call):
        try:
            barrier.wait(timeout=10)
            results[name] = call()
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=run, args=("first", first)),
        threading.Thread(target=run, args=("second", second)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert failures == []
    return results["first"], results["second"]


def ends(teacher, advising):
    return lambda: end_advising(Client(), teacher, advising.id)


def assert_nothing_open_in_the_ended_advising(advising):
    assert Advising.objects.get(pk=advising.pk).status == AdvisingStatus.ENDED
    assert not Meeting.objects.filter(advising=advising, status__in=OPEN_MEETING).exists()
    assert not Activity.objects.filter(advising=advising, status__in=OPEN_ACTIVITY).exists()


class TestTheEndingRacingWithAMeeting:
    def test_whoever_wins_the_lock_no_meeting_is_left_open(self):
        for _ in range(RACES):
            student, teacher, advising = advised_pair()

            created, ended = race(
                lambda s=student: post_json(
                    Client(), "/api/reunioes", meeting_body(), headers=bearer(s.user)
                ),
                ends(teacher, advising),
            )

            assert ended.status_code == 200
            assert created.status_code in (201, 409)
            meetings = Meeting.objects.filter(advising=advising)
            if created.status_code == 409:
                assert not meetings.exists()
            else:
                (meeting,) = meetings
                assert meeting.status == MeetingStatus.CANCELLED
                assert meeting.cancelled_by_system is True
            assert_nothing_open_in_the_ended_advising(advising)

    def test_a_meeting_created_before_the_ending_gets_the_lock_is_cancelled_by_the_ending(self):
        student, teacher, advising = advised_pair()
        failures = []
        ending = []

        def end_while_the_meeting_is_being_written():
            try:
                ending.append(ends(teacher, advising)())
            except Exception as error:  # noqa: BLE001 - reported to the main thread
                failures.append(error)
            finally:
                connection.close()

        with transaction.atomic():
            locked = Advising.objects.select_for_update().get(pk=advising.pk)
            thread = threading.Thread(target=end_while_the_meeting_is_being_written)
            thread.start()
            wait_for_a_request_blocked_on_a_lock(timeout=20)
            Meeting.objects.create(
                advising=locked,
                author=student.user,
                scheduled_at=timezone.now() + datetime.timedelta(days=3),
                ends_at=timezone.now() + datetime.timedelta(days=3, hours=1),
                description="Criada com a trava da orientação",
                status=MeetingStatus.ACCEPTED,
            )
        thread.join(timeout=20)

        assert failures == []
        assert ending[0].status_code == 200
        (meeting,) = Meeting.objects.filter(advising=advising)
        assert meeting.status == MeetingStatus.CANCELLED
        assert meeting.cancelled_by_system is True
        assert_nothing_open_in_the_ended_advising(advising)


class TestTheEndingRacingWithAnActivity:
    def test_whoever_wins_the_lock_no_activity_is_left_open(self):
        for _ in range(RACES):
            student, teacher, advising = advised_pair()

            created, ended = race(
                lambda s=student, t=teacher: post_json(
                    Client(), "/api/atividades", activity_body(s), headers=bearer(t.user)
                ),
                ends(teacher, advising),
            )

            assert ended.status_code == 200
            assert created.status_code in (201, 404)
            activities = Activity.objects.filter(advising=advising)
            if created.status_code == 404:
                assert not activities.exists()
            else:
                (activity,) = activities
                assert activity.status == ActivityStatus.ARCHIVED
            assert_nothing_open_in_the_ended_advising(advising)

    def test_an_activity_created_before_the_ending_gets_the_lock_is_archived_by_the_ending(self):
        student, teacher, advising = advised_pair()
        failures = []
        ending = []

        def end_while_the_activity_is_being_written():
            try:
                ending.append(ends(teacher, advising)())
            except Exception as error:  # noqa: BLE001 - reported to the main thread
                failures.append(error)
            finally:
                connection.close()

        with transaction.atomic():
            locked = Advising.objects.select_for_update().get(pk=advising.pk)
            thread = threading.Thread(target=end_while_the_activity_is_being_written)
            thread.start()
            wait_for_a_request_blocked_on_a_lock(timeout=20)
            Activity.objects.create(
                advising=locked,
                title="Capítulo 2",
                description="Criada com a trava da orientação",
                deadline=timezone.localdate() + datetime.timedelta(days=10),
            )
        thread.join(timeout=20)

        assert failures == []
        assert ending[0].status_code == 200
        (activity,) = Activity.objects.filter(advising=advising)
        assert activity.status == ActivityStatus.ARCHIVED
        assert_nothing_open_in_the_ended_advising(advising)

    def test_after_the_ending_a_new_activity_or_meeting_is_refused(self, client):
        student, teacher, advising = advised_pair()
        assert ends(teacher, advising)().status_code == 200

        activity = post_json(
            client, "/api/atividades", activity_body(student), headers=bearer(teacher.user)
        )
        meeting = post_json(client, "/api/reunioes", meeting_body(), headers=bearer(student.user))

        assert activity.status_code == 404
        assert meeting.status_code == 409
        assert not Activity.objects.filter(advising=advising).exists()
        assert not Meeting.objects.filter(advising=advising).exists()
