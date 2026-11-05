from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone

from config.errors import FieldValidationError

from .models import AreaOfInterest, PublishedArticle, Student
from .schemas import ArticleIn, StudentProfileIn

User = get_user_model()

MAX_ARTICLES = 50
FIRST_ARTICLE_YEAR = 1900
_url_validator = URLValidator(schemes=["http", "https"])
READ_ONLY_FIELDS = frozenset(
    {"email", "password", "registration_number", "institute_id", "course_id", "course_ids"}
)


def _validate_full_name(full_name: str) -> dict[str, str]:
    max_length = User._meta.get_field("full_name").max_length
    if not full_name:
        return {"full_name": "Informe o nome completo."}
    if len(full_name) > max_length:
        return {"full_name": f"O nome deve ter no máximo {max_length} caracteres."}
    return {}


def _validate_area_ids(area_ids: list[int]) -> dict[str, str]:
    unique_ids = set(area_ids)
    if not unique_ids:
        return {"area_ids": "Escolha ao menos uma área de interesse."}
    if AreaOfInterest.objects.filter(id__in=unique_ids).count() != len(unique_ids):
        return {"area_ids": "Área de interesse não encontrada."}
    return {}


def _article_error(article: ArticleIn, current_year: int) -> str | None:
    title_max = PublishedArticle._meta.get_field("title").max_length
    url_max = PublishedArticle._meta.get_field("url").max_length
    if not article.title:
        return "Informe o título de cada artigo."
    if len(article.title) > title_max:
        return f"O título do artigo deve ter no máximo {title_max} caracteres."
    if not FIRST_ARTICLE_YEAR <= article.year <= current_year:
        return f"O ano do artigo deve estar entre {FIRST_ARTICLE_YEAR} e o ano atual."
    if article.url:
        try:
            _url_validator(article.url)
        except ValidationError:
            valid_url = False
        else:
            valid_url = len(article.url) <= url_max
        if not valid_url:
            return (
                "A URL do artigo deve começar com http:// ou https:// "
                f"e ter no máximo {url_max} caracteres."
            )
    return None


def _validate_articles(articles: list[ArticleIn]) -> dict[str, str]:
    if len(articles) > MAX_ARTICLES:
        return {"articles": f"Informe no máximo {MAX_ARTICLES} artigos."}
    current_year = timezone.localdate().year
    for article in articles:
        message = _article_error(article, current_year)
        if message:
            return {"articles": message}
    return {}


def _validate_extra_fields(data: StudentProfileIn) -> dict[str, str]:
    return {
        field: (
            "Este campo não pode ser alterado."
            if field in READ_ONLY_FIELDS
            else "Campo não reconhecido."
        )
        for field in data.model_extra or {}
    }


@transaction.atomic
def update_student_profile(student: Student, data: StudentProfileIn) -> Student:
    errors = _validate_extra_fields(data)
    if "full_name" in data.model_fields_set:
        errors.update(_validate_full_name(data.full_name or ""))
    if "area_ids" in data.model_fields_set:
        errors.update(_validate_area_ids(data.area_ids or []))
    if "articles" in data.model_fields_set:
        errors.update(_validate_articles(data.articles or []))
    if errors:
        raise FieldValidationError(errors)

    if "articles" in data.model_fields_set:
        student.articles.all().delete()
        PublishedArticle.objects.bulk_create(
            PublishedArticle(student=student, title=a.title, year=a.year, url=a.url)
            for a in data.articles or []
        )
    if "area_ids" in data.model_fields_set:
        student.areas_of_interest.set(data.area_ids)
    if "full_name" in data.model_fields_set:
        student.user.full_name = data.full_name
        student.user.save(update_fields=["full_name"])
    return student
