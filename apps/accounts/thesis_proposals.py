from django.db import transaction
from django.db.models import QuerySet
from ninja.errors import HttpError

from config.errors import ConflictError, FieldValidationError

from .models import Student, Teacher, ThesisProposal, normalize_title

PROPOSALS_PER_TEACHER = 50
TITLE_TAKEN_MESSAGE = "Você já tem uma proposta com esse título."
LIMIT_REACHED_MESSAGE = (
    f"Você já tem {PROPOSALS_PER_TEACHER} propostas de tema. Remova uma para cadastrar outra."
)
PROPOSAL_NOT_FOUND_MESSAGE = "Proposta de tema não encontrada."
TEACHER_NOT_FOUND_MESSAGE = "Professor não encontrado."


def list_proposals(teacher: Teacher) -> QuerySet[ThesisProposal]:
    """The teacher's proposals, newest first and then by id, stable across pages (RF56)."""
    return ThesisProposal.objects.filter(teacher=teacher).order_by("-created_at", "-id")


def list_proposals_for_student(student: Student, teacher_id: int) -> QuerySet[ThesisProposal]:
    """The proposals of an active teacher of the student's course (RN28, RN42).

    Another course, an inactive teacher and an unknown id get the same 404, so the student learns
    nothing about who exists.
    """
    teacher = Teacher.objects.filter(
        user_id=teacher_id, courses=student.course_id, user__is_active=True
    ).first()
    if teacher is None:
        raise HttpError(404, TEACHER_NOT_FOUND_MESSAGE)
    return list_proposals(teacher)


def _lock(teacher: Teacher) -> None:
    """Serializes the writes of one teacher, so the limit of 50 holds under concurrency."""
    Teacher.objects.select_for_update().get(pk=teacher.pk)


def _ensure_title_is_free(teacher: Teacher, title: str, exclude_id: int | None = None) -> None:
    """The title is free for this teacher, ignoring case, accents and the ends (RN41).

    The teacher row is locked by the caller, so two writes of the same teacher cannot race past
    this check; the unique constraint stays as the last line of defence.
    """
    taken = ThesisProposal.objects.filter(teacher=teacher, title_key=normalize_title(title))
    if exclude_id is not None:
        taken = taken.exclude(pk=exclude_id)
    if taken.exists():
        raise FieldValidationError({"title": TITLE_TAKEN_MESSAGE})


@transaction.atomic
def create_proposal(teacher: Teacher, title: str) -> ThesisProposal:
    _lock(teacher)
    if ThesisProposal.objects.filter(teacher=teacher).count() >= PROPOSALS_PER_TEACHER:
        raise ConflictError(LIMIT_REACHED_MESSAGE)
    _ensure_title_is_free(teacher, title)
    return ThesisProposal.objects.create(teacher=teacher, title=title)


def _own(teacher: Teacher, proposal_id: int) -> ThesisProposal:
    proposal = ThesisProposal.objects.filter(pk=proposal_id, teacher=teacher).first()
    if proposal is None:
        raise HttpError(404, PROPOSAL_NOT_FOUND_MESSAGE)
    return proposal


@transaction.atomic
def edit_proposal(teacher: Teacher, proposal_id: int, title: str) -> ThesisProposal:
    _lock(teacher)
    proposal = _own(teacher, proposal_id)
    _ensure_title_is_free(teacher, title, exclude_id=proposal.pk)
    proposal.title = title
    proposal.save()
    return proposal


@transaction.atomic
def delete_proposal(teacher: Teacher, proposal_id: int) -> None:
    """The removal is definitive (RF55)."""
    _lock(teacher)
    _own(teacher, proposal_id).delete()
