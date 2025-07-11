from ninja.openapi.docs import Swagger
from ninja_extra import NinjaExtraAPI

from academic.controllers import ExpertiseAreaController
from academic.controllers import InstituteController
from tcc.controllers import SupervisionRequestController
from user.controllers import ProfessorController
from user.controllers import StudentController
from user.controllers import UserController

docs = Swagger(settings={'docExpansion': 'none'})

api = NinjaExtraAPI(title='SIGTCC', version='1.0.0', docs=docs)
api.register_controllers(ExpertiseAreaController)
api.register_controllers(InstituteController)
api.register_controllers(ProfessorController)
api.register_controllers(StudentController)
api.register_controllers(SupervisionRequestController)
api.register_controllers(UserController)
