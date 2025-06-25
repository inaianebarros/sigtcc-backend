from django.http.request import HttpRequest
from ninja.constants import NOT_SET
from ninja_extra import api_controller
from ninja_extra import ControllerBase
from ninja_extra import route
from ninja_extra import status

from academic.models import ExpertiseArea
from academic.models import Institute
from academic.schemas import ExpertiseAreaSchemaOut
from academic.schemas import InstituteSchemaOut
from sigtcc.schemas import ReturnSchema


@api_controller('/expertise-areas', auth=NOT_SET, permissions=[], tags=['Expertise Areas'])
class ExpertiseAreaController(ControllerBase):
    @route.get(
        '/',
        response={
            status.HTTP_200_OK: list[ExpertiseAreaSchemaOut],
            status.HTTP_500_INTERNAL_SERVER_ERROR: ReturnSchema,
        },
    )
    def list_expertise_areas(self):
        return ExpertiseArea.objects.all().order_by('name')


@api_controller('/institutes', auth=NOT_SET, permissions=[], tags=['Institutes'])
class InstituteController(ControllerBase):
    @route.get(
        '/',
        response={
            status.HTTP_200_OK: list[InstituteSchemaOut],
            status.HTTP_500_INTERNAL_SERVER_ERROR: ReturnSchema,
        },
    )
    def list_institutes(self):
        return Institute.objects.all().order_by('name')
