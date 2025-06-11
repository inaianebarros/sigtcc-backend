from ninja import ModelSchema

from user.models import User


class UserSchemaIn(ModelSchema):
    class Meta:
        model = User
        fields = ['first_name', 'email', 'password']
