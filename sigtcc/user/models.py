from django.contrib.auth.models import AbstractUser
from django.db.models import CASCADE
from django.db.models import CharField
from django.db.models import DO_NOTHING
from django.db.models import ForeignKey
from django.db.models import ManyToManyField
from django.db.models import TextChoices

from academic.models import Course
from academic.models import ExpertiseArea
from academic.models import Institute
from core.base_models import BaseModelWithHistory


class ProfessorProfile(BaseModelWithHistory):
    expertise_areas = ManyToManyField(
        ExpertiseArea,
        related_name='professors',
        related_query_name='professor',
        verbose_name='Areas de Atuação',
    )
    institute = ForeignKey(
        Institute,
        on_delete=CASCADE,
        related_name='professors',
        related_query_name='professor',
        verbose_name='Instituto',
    )
    user = ForeignKey(
        'User',
        on_delete=CASCADE,
        related_name='professor_profiles',
        related_query_name='professor_profile',
        verbose_name='Usuário',
    )


class StudentProfile(BaseModelWithHistory):
    course = ForeignKey(
        Course,
        on_delete=DO_NOTHING,
        related_name='students',
        related_query_name='student',
        verbose_name='Curso',
    )
    enrollment = CharField(max_length=50, unique=True)
    user = ForeignKey(
        'User',
        on_delete=CASCADE,
        related_name='student_profiles',
        related_query_name='student_profile',
        verbose_name='Usuário',
    )


class User(BaseModelWithHistory, AbstractUser):
    class ROLE(TextChoices):
        PROFESSOR = 'PROFESSOR', 'Professor'
        STUDENT = 'STUDENT', 'Student'

    role = CharField(max_length=10, choices=ROLE.choices, default=ROLE.STUDENT)
