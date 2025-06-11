from ninja.openapi.docs import Swagger
from ninja_extra import NinjaExtraAPI

from user.controllers import UserController

docs = Swagger(settings={'docExpansion': 'none'})

api = NinjaExtraAPI(title='SIGTCC', version='1.0.0', docs=docs)
api.register_controllers(UserController)
