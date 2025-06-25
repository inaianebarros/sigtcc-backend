from django.core.management import call_command
from django.core.management.base import BaseCommand

from common.management.commands.populator import Populator


class Command(BaseCommand):
    help = 'DELETE ALL DATA FROM DB and populate the database with fake data'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting flush...'))
        call_command('flush', interactive=False)
        self.stdout.write(self.style.SUCCESS('Finished flush...'))

        self.stdout.write(self.style.SUCCESS('Starting populate...'))
        populator = Populator()
        populator.execute(command=self)
        self.stdout.write(self.style.SUCCESS('Finished populate...'))
