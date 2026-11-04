from ninja import Schema

from .models import Student


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


class StudentRegistrationIn(Schema):
    registration_number: str
    full_name: str
    email: str
    password: str
    password_confirmation: str
    institute_id: int
    course_id: int
    area_ids: list[int]


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
