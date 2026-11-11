from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.demo_activities import seed_activity_scenarios
from apps.accounts.demo_history import seed_ended_advising
from apps.accounts.demo_scenarios import seed_active_advisings, seed_request_scenarios
from apps.accounts.demo_seed import DEMO_PASSWORD, DemoSeedError, seed_demo_users
from apps.accounts.demo_volume import seed_volume
from config.errors import FieldValidationError


class Command(BaseCommand):
    help = (
        "Creates the demonstration users (students, teachers and an administrator) to try every "
        "screen. Local use only; safe to run again: it creates only what is missing."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--volume",
            action="store_true",
            help="Also adds teachers and ended advisings in volume, to try the pagination.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "Os dados de demonstração têm uma senha conhecida e só podem ser criados em "
                "ambiente local: defina DJANGO_DEBUG=true."
            )
        try:
            report = seed_demo_users()
            created_requests = seed_request_scenarios()
            seed_active_advisings()
            created_activities, skipped = seed_activity_scenarios()
            seed_ended_advising()
            volume_counts = seed_volume() if options["volume"] else None
        except DemoSeedError as error:
            raise CommandError(str(error)) from error
        except FieldValidationError as error:
            raise CommandError(
                f"Não foi possível criar um usuário de demonstração: {error.errors}. "
                "Nada foi criado."
            ) from error
        for email, created in report:
            self.stdout.write(f"{email}{'' if created else ' (já existe)'}")
        self.stdout.write(f"Solicitações de orientação criadas agora: {created_requests}")
        self.stdout.write(f"Atividades criadas agora: {created_activities}")
        if skipped:
            self.stdout.write(
                "Armazenamento de arquivos indisponível: foram puladas as atividades que "
                f"precisam de entregas ({', '.join(skipped)}). Rode o comando de novo com o "
                "armazenamento no ar."
            )
        if volume_counts is not None:
            teachers, advisings = volume_counts
            if teachers or advisings:
                self.stdout.write(
                    f"Volume: {teachers} professores e {advisings} orientações encerradas "
                    "criados agora."
                )
            else:
                self.stdout.write("Volume: nenhum dado novo (já existe).")
        self.stdout.write(f"Senha de todos os usuários de demonstração: {DEMO_PASSWORD}")
