from django.core.management.base import BaseCommand
from faker import Faker

from academic.models import ExpertiseArea
from academic.models import Institute
from user.models import ProfessorProfile
from user.models import User


class Populator:
    def __init__(self):
        self.fake = Faker('pt_BR')

    def _all_expertise_areas(self) -> list[ExpertiseArea]:
        return ExpertiseArea.objects.all()

    def _all_institutes(self) -> list[Institute]:
        return Institute.objects.all()

    def _create_user(self, type_user) -> User:
        email = self.fake.email()
        user = User(email=email, first_name=self.fake.name(), username=email)
        user.set_password(self.fake.password())

        if type_user:
            user.type_user = type_user
            user.save()

        return user

    def populate_expertise_area(self) -> None:
        self.command.stdout.write(self.command.style.WARNING('Populating Institute...'))
        try:
            for _ in range(10):
                ExpertiseArea.objects.create(name=self.fake.word())
        except Exception as exc:
            self.command.stdout.write(
                self.command.style.ERROR(f'Failed to populate Institute: {exc}')
            )
        self.command.stdout.write(self.command.style.SUCCESS('Finished populate Institute'))

    def populate_institute(self) -> None:
        self.command.stdout.write(self.command.style.WARNING('Populating Institute...'))
        try:
            for name in ['IEG', 'IBEF', 'ICED', 'ICTA', 'ISCO']:
                Institute.objects.create(name=name)
        except Exception as exc:
            self.command.stdout.write(
                self.command.style.ERROR(f'Failed to populate Institute: {exc}')
            )
        self.command.stdout.write(self.command.style.SUCCESS('Finished populate Institute'))

    def populate_professor(self) -> None:
        self.command.stdout.write(self.command.style.WARNING('Populating Professor...'))
        try:
            for _ in range(100):
                user = self._create_user(User.ROLE.PROFESSOR)

                professor_profile = ProfessorProfile.objects.create(
                    institute=self.fake.random_element(self._all_institutes()),
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

    def execute(self, command: BaseCommand):
        self.command = command
        chain = [
            self.populate_expertise_area,
            self.populate_institute,
            self.populate_professor,
        ]
        for func in chain:
            func()
