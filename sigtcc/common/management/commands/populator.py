from uuid import uuid4

from django.core.management.base import BaseCommand
from faker import Faker

from academic.models import ExpertiseArea
from academic.models import Course
from academic.models import Institute
from user.models import ProfessorProfile
from user.models import StudentProfile
from user.models import User
from tcc.models import SupervisionRequest


class Populator:
    def __init__(self):
        self.fake = Faker('pt_BR')

    def _all_courses(self) -> list[Course]:
        return Course.objects.all()

    def _all_expertise_areas(self) -> list[ExpertiseArea]:
        return ExpertiseArea.objects.all()

    def _all_institutes(self) -> list[Institute]:
        return Institute.objects.all()

    def _all_professors(self) -> list[ProfessorProfile]:
        return ProfessorProfile.objects.all()

    def _create_user(self, type_user) -> User:
        email = self.fake.unique.email()
        user = User(email=email, first_name=self.fake.name(), username=email)
        user.set_password('1234567890')

        if type_user:
            user.role = type_user
            user.save()

        return user

    def _per_cent_number(self) -> int:
        return self.fake.random_int(min=0, max=100)

    def populate_course(self) -> None:
        self.command.stdout.write(self.command.style.WARNING('Populating Course...'))
        for _ in range(10):
            try:
                Course.objects.create(
                    description=self.fake.text(),
                    institute=self.fake.random_element(self._all_institutes()),
                    name=self.fake.unique.word(),
                )
            except Exception as exc:
                self.command.stdout.write(
                    self.command.style.ERROR(f'Failed to populate Course: {exc}')
                )
        self.command.stdout.write(self.command.style.SUCCESS('Finished populate Course'))

    def populate_expertise_area(self) -> None:
        self.command.stdout.write(self.command.style.WARNING('Populating Expertise Area...'))
        try:
            for _ in range(10):
                ExpertiseArea.objects.create(name=self.fake.unique.word())
        except Exception as exc:
            self.command.stdout.write(
                self.command.style.ERROR(f'Failed to populate Expertise Area: {exc}')
            )
        self.command.stdout.write(self.command.style.SUCCESS('Finished populate Expertise Area'))

    def populate_institute(self) -> None:
        self.command.stdout.write(self.command.style.WARNING('Populating Institute...'))
        for name in [
            'Instituto de Engenharia e Geociências - IEG',
            'Instituto de Ciências da Sociedade - ICS',
            'Instituto de Ciências da Educação - ICED',
            'Instituto de Biodiversidade e Florestas - IBEF',
            'Instituto de Ciências e Tecnologia das Águas - ICTA',
            'Instituto de Saúde Coletiva - ISCO',
            'Instituto de Formação Interdisciplinar e Intercultural - IFII',
        ]:
            try:
                Institute.objects.create(name=name)
            except Exception as exc:
                self.command.stdout.write(
                    self.command.style.ERROR(f'Failed to populate Institute: {exc}')
                )
        self.command.stdout.write(self.command.style.SUCCESS('Finished populate Institute'))

    def populate_professor(self) -> None:
        self.command.stdout.write(self.command.style.WARNING('Populating Professor...'))
        for _ in range(100):
            try:
                user = self._create_user(User.ROLE.PROFESSOR.value)

                professor_profile = ProfessorProfile.objects.create(
                    biography=self.fake.text(),
                    institute=self.fake.random_element(self._all_institutes()),
                    lattes_url=f'https://lattes.cnpq.br/{uuid4()}',
                    user=user,
                )

                for _ in range(3):
                    professor_profile.expertise_areas.add(
                        self.fake.random_element(self._all_expertise_areas())
                    )

                professor_profile.save()
            except Exception as exc:
                self.command.stdout.write(
                    self.command.style.ERROR(f'Failed to populate Professor: {exc}')
                )
        self.command.stdout.write(self.command.style.SUCCESS('Finished populate Professor'))

    def populate_student(self) -> None:
        self.command.stdout.write(self.command.style.WARNING('Populating Student...'))
        for _ in range(100):
            try:
                user = self._create_user(User.ROLE.STUDENT)

                StudentProfile.objects.create(
                    course=self.fake.random_element(self._all_courses()),
                    user=user,
                )
            except Exception as exc:
                self.command.stdout.write(
                    self.command.style.ERROR(f'Failed to populate Student: {exc}')
                )
        self.command.stdout.write(self.command.style.SUCCESS('Finished populate Student'))

    def populate_student_request(self) -> None:
        self.command.stdout.write(self.command.style.WARNING('Populating Student Request...'))

        answer = SupervisionRequest.ANSWER.YES

        for student in StudentProfile.objects.all():
            if self._per_cent_number() <= 80:
                if self._per_cent_number() <= 20:
                    answer = SupervisionRequest.ANSWER.YES
                try:
                    SupervisionRequest.objects.create(
                        answer=answer,
                        professor=self.fake.random_element(self._all_professors()),
                        professor_message=self.fake.text(),
                        student=student,
                        student_message=self.fake.text(),
                    )
                except Exception as exc:
                    self.command.stdout.write(
                        self.command.style.ERROR(f'Failed to populate Student Request: {exc}')
                    )
        self.command.stdout.write(self.command.style.SUCCESS('Finished populate Student Request'))

    def execute(self, command: BaseCommand):
        self.command = command
        chain = [
            self.populate_expertise_area,
            self.populate_institute,
            self.populate_course,
            self.populate_professor,
            self.populate_student,
            self.populate_student_request,
        ]
        for func in chain:
            func()
