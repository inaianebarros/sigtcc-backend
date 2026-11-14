"""The reset of the demonstration data: it removes only what is demonstration and recreates it (#144)."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.demo_seed import DEMO_DOMAIN, DEMO_PASSWORD
from apps.accounts.demo_volume import VOLUME_PREFIX
from apps.accounts.models import AreaOfInterest, Course, Institute, Student, Teacher, User
from apps.activities.models import Activity, ReviewFile, SubmissionFile
from apps.activities.storage import submission_storage
from apps.advising.models import Advising, AdvisingRequest
from apps.meetings.models import Meeting
from apps.notifications.models import Notification
from tests.api.helpers import LOGIN_URL, post_json
from tests.factories import (
    make_activity,
    make_advising,
    make_advising_request,
    make_notification,
    make_student,
    make_teacher,
)

pytestmark = pytest.mark.django_db

CAST_SIZE = 13
PENDING_STUDENT = "aluno2.solicitacao-pendente"
COMPLETE_STUDENT = "aluno5.orientacao-completa"


def run(*options, answer=None):
    out = StringIO()
    if answer is None:
        call_command("seed_demo_data", *options, stdout=out)
    else:
        with patch("builtins.input", return_value=answer) as asked:
            call_command("seed_demo_data", *options, stdout=out)
        out.write(f"\nasked={asked.call_count} prompt={asked.call_args}")
    return out.getvalue()


def demo_users():
    return User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}")


@pytest.fixture
def seeded(catalog, debug):
    run()


def demo_ids():
    return set(demo_users().values_list("id", flat=True))


def user_of(slug):
    return User.objects.get(email=f"{slug}@{DEMO_DOMAIN}")


def test_the_reset_recreates_the_whole_set_with_new_rows(seeded):
    old_ids = demo_ids()

    run("--reset", "--yes")

    assert demo_users().count() == CAST_SIZE
    assert not old_ids & demo_ids()
    assert Activity.objects.filter(advising__student__user=user_of(COMPLETE_STUDENT)).count() == 6


def test_the_reset_renews_the_relative_dates(seeded):
    AdvisingRequest.objects.filter(student__user=user_of(PENDING_STUDENT)).update(
        requested_at=timezone.now() - timedelta(days=40)
    )

    run("--reset", "--yes")

    renewed = AdvisingRequest.objects.get(student__user=user_of(PENDING_STUDENT))
    assert timezone.now() - renewed.requested_at < timedelta(days=7)


def test_the_demonstration_users_still_sign_in_after_the_reset(browser, seeded):
    run("--reset", "--yes")

    response = post_json(
        browser,
        LOGIN_URL,
        {"email": f"aluno1.sem-orientacao@{DEMO_DOMAIN}", "password": DEMO_PASSWORD},
    )

    assert response.status_code == 200


def test_the_reset_leaves_the_other_users_and_their_data_untouched(seeded):
    course = Course.objects.get(name="Ciência da Computação")
    teacher = make_teacher(courses=[course], full_name="Professora Real")
    student = make_student(course=course, full_name="Aluno Real")
    advising = make_advising(student=student, teacher=teacher)
    activity = make_activity(advising)
    make_notification(student.user)
    other_request = make_advising_request()

    run("--reset", "--yes")

    assert User.objects.filter(pk__in=[teacher.user_id, student.user_id]).count() == 2
    assert Advising.objects.filter(pk=advising.pk).exists()
    assert Activity.objects.filter(pk=activity.pk).exists()
    assert Notification.objects.filter(recipient=student.user).count() == 1
    assert AdvisingRequest.objects.filter(pk=other_request.pk).exists()


def test_the_reset_does_not_touch_the_catalog_nor_the_extra_course(seeded):
    before = (Institute.objects.count(), Course.objects.count(), AreaOfInterest.objects.count())

    run("--reset", "--yes")

    assert before == (
        Institute.objects.count(),
        Course.objects.count(),
        AreaOfInterest.objects.count(),
    )
    assert Course.objects.filter(name="Sistemas de Informação").count() == 1


def test_it_removes_everything_that_belongs_to_the_demonstration_users(seeded):
    counts_before = (SubmissionFile.objects.count(), Meeting.objects.count())
    assert all(counts_before)

    run("--reset", "--yes")

    # The same set comes back, so the counts are the same, but from new rows.
    assert (SubmissionFile.objects.count(), Meeting.objects.count()) == counts_before
    assert Student.objects.count() == 8
    assert Teacher.objects.count() == 4


def test_the_files_of_the_deliveries_are_removed_from_the_storage(seeded):
    storage = submission_storage()
    keys = list(SubmissionFile.objects.values_list("storage_key", flat=True))
    assert keys and all(storage.exists(key) for key in keys)

    run("--reset", "--yes")

    assert not any(storage.exists(key) for key in keys)


def test_a_storage_that_fails_to_delete_does_not_stop_the_reset(seeded):
    with patch("apps.accounts.demo_reset.submission_storage") as storage:
        storage.return_value.delete.side_effect = ConnectionError("armazenamento fora do ar")
        run("--reset", "--yes")

    assert demo_users().count() == CAST_SIZE


def test_without_yes_it_shows_how_many_users_it_removes_and_asks(seeded):
    output = run("--reset", answer="s")

    assert "asked=1" in output
    assert str(CAST_SIZE) in output.split("prompt=")[1]
    assert demo_users().count() == CAST_SIZE


def test_a_negative_answer_cancels_and_changes_nothing(seeded):
    old_ids = demo_ids()

    output = run("--reset", answer="n")

    assert "cancelado" in output.lower()
    assert demo_ids() == old_ids


def test_without_yes_and_without_a_terminal_it_asks_for_yes_and_changes_nothing(seeded):
    old_ids = demo_ids()

    with patch("builtins.input", side_effect=EOFError), pytest.raises(CommandError, match="--yes"):
        call_command("seed_demo_data", "--reset", stdout=StringIO())

    assert demo_ids() == old_ids


def test_a_failure_in_the_middle_of_the_removal_undoes_everything(seeded):
    old_ids = demo_ids()
    counts = (Activity.objects.count(), Meeting.objects.count(), Notification.objects.count())

    with (
        patch("apps.accounts.demo_reset._delete_notifications", side_effect=RuntimeError("falha")),
        pytest.raises(RuntimeError),
    ):
        run("--reset", "--yes")

    assert demo_ids() == old_ids
    assert counts == (
        Activity.objects.count(),
        Meeting.objects.count(),
        Notification.objects.count(),
    )


def test_it_refuses_while_a_real_user_is_tied_to_a_demonstration_user(seeded):
    course = Course.objects.get(name="Ciência da Computação")
    real_student = make_student(course=course, full_name="Aluno Real")
    demo_teacher = user_of("prof2.sem-orientandos").teacher
    AdvisingRequest.objects.create(student=real_student, teacher=demo_teacher)
    old_ids = demo_ids()

    with pytest.raises(CommandError, match="usuários reais"):
        run("--reset", "--yes")

    assert demo_ids() == old_ids
    assert AdvisingRequest.objects.filter(student=real_student).exists()


def test_it_refuses_without_the_debug_mode_even_with_yes(catalog, settings):
    settings.DEBUG = False

    with pytest.raises(CommandError, match="DJANGO_DEBUG"):
        run("--reset", "--yes")

    assert User.objects.count() == 0


def test_the_reset_also_removes_the_volume_data(seeded):
    run("--volume")
    assert User.objects.filter(email__startswith=VOLUME_PREFIX).exists()

    run("--reset", "--yes")

    assert not User.objects.filter(email__startswith=VOLUME_PREFIX).exists()
    assert demo_users().count() == CAST_SIZE


def test_the_reset_with_volume_recreates_the_volume_data(seeded):
    run("--volume")
    volume_before = User.objects.filter(email__startswith=VOLUME_PREFIX).count()

    run("--reset", "--volume", "--yes")

    assert User.objects.filter(email__startswith=VOLUME_PREFIX).count() == volume_before


def test_with_nothing_to_remove_it_just_creates_the_set_without_asking(catalog, debug):
    output = run("--reset", answer="s")

    assert "asked=0" in output
    assert "nenhum dado de demonstração" in output.lower()
    assert demo_users().count() == CAST_SIZE


def test_the_reset_twice_in_a_row_works(seeded):
    run("--reset", "--yes")
    run("--reset", "--yes")

    assert demo_users().count() == CAST_SIZE


def test_the_yes_option_alone_does_nothing_special(seeded):
    old_ids = demo_ids()

    run("--yes")

    assert demo_ids() == old_ids


def test_the_refusal_comes_before_the_question(seeded):
    real_student = make_student(course=Course.objects.get(name="Ciência da Computação"))
    AdvisingRequest.objects.create(
        student=real_student, teacher=user_of("prof2.sem-orientandos").teacher
    )

    with patch("builtins.input") as asked, pytest.raises(CommandError, match="usuários reais"):
        call_command("seed_demo_data", "--reset", stdout=StringIO())

    asked.assert_not_called()


def test_the_attachments_of_the_refusals_are_removed_from_the_storage(seeded):
    storage = submission_storage()
    keys = list(ReviewFile.objects.values_list("storage_key", flat=True))
    assert keys and all(storage.exists(key) for key in keys)

    run("--reset", "--yes")

    assert not any(storage.exists(key) for key in keys)
