from typing import Annotated

from ninja import Schema
from pydantic import StringConstraints

from .models import Student, Teacher


class InstituteOut(Schema):
    id: int
    name: str


class CourseOut(Schema):
    id: int
    institute_id: int
    name: str


class AreaOfInterestOut(Schema):
    id: int
    name: str


StrippedStr = Annotated[str, StringConstraints(strip_whitespace=True)]


class ProfileRegistrationIn(Schema):
    registration_number: StrippedStr
    full_name: StrippedStr
    email: StrippedStr
    password: str
    password_confirmation: str
    institute_id: int
    area_ids: list[int]


class StudentRegistrationIn(ProfileRegistrationIn):
    course_id: int


class TeacherRegistrationIn(ProfileRegistrationIn):
    course_ids: list[int]


class StudentOut(Schema):
    id: int
    full_name: str
    email: str
    type: str
    registration_number: str
    institute_id: int
    course_id: int
    area_ids: list[int]

    @classmethod
    def from_student(cls, student: Student) -> "StudentOut":
        return cls(
            id=student.user_id,
            full_name=student.user.full_name,
            email=student.user.email,
            type=student.user.type,
            registration_number=student.registration_number,
            institute_id=student.course.institute_id,
            course_id=student.course_id,
            area_ids=list(student.areas_of_interest.values_list("id", flat=True)),
        )


class TeacherOut(Schema):
    id: int
    full_name: str
    email: str
    type: str
    registration_number: str
    institute_id: int
    course_ids: list[int]
    area_ids: list[int]
    is_available: bool

    @classmethod
    def from_teacher(cls, teacher: Teacher, institute_id: int) -> "TeacherOut":
        return cls(
            id=teacher.user_id,
            full_name=teacher.user.full_name,
            email=teacher.user.email,
            type=teacher.user.type,
            registration_number=teacher.registration_number,
            institute_id=institute_id,
            course_ids=list(teacher.courses.values_list("id", flat=True)),
            area_ids=list(teacher.areas_of_interest.values_list("id", flat=True)),
            is_available=teacher.is_available,
        )
