from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.demo_seed import DEMO_PASSWORD, DemoSeedError, seed_demo_users
from config.errors import FieldValidationError


class Command(BaseCommand):
    help = (
        "Creates the demonstration users (students, teachers and an administrator) to try every "
        "screen. Local use only; safe to run again: it creates only what is missing."
    )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "Os dados de demonstração têm uma senha conhecida e só podem ser criados em "
                "ambiente local: defina DJANGO_DEBUG=true."
            )
        try:
            report = seed_demo_users()
        except DemoSeedError as error:
            raise CommandError(str(error)) from error
        except FieldValidationError as error:
            raise CommandError(
                f"Não foi possível criar um usuário de demonstração: {error.errors}. "
                "Nada foi criado."
            ) from error
        for email, created in report:
            self.stdout.write(f"{email}{'' if created else ' (já existe)'}")
        self.stdout.write(f"Senha de todos os usuários de demonstração: {DEMO_PASSWORD}")
