from collections.abc import Collection, Iterable

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils import timezone

from .models import AreaOfInterest, PublishedArticle
from .schemas import ArticleIn

User = get_user_model()

MAX_ARTICLES = 50
FIRST_ARTICLE_YEAR = 1900
NULL_ARTICLES_MESSAGE = "Envie a lista de artigos; use uma lista vazia para removê-los todos."
_url_validator = URLValidator(schemes=["http", "https"])


def validate_full_name(full_name: str) -> dict[str, str]:
    max_length = User._meta.get_field("full_name").max_length
    if not full_name:
        return {"full_name": "Informe o nome completo."}
    if len(full_name) > max_length:
        return {"full_name": f"O nome deve ter no máximo {max_length} caracteres."}
    return {}


def validate_area_ids(area_ids: Collection[int]) -> dict[str, str]:
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
    if article.year is None or not FIRST_ARTICLE_YEAR <= article.year <= current_year:
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


def validate_articles(articles: list[ArticleIn] | None) -> dict[str, str]:
    if articles is None:
        return {"articles": NULL_ARTICLES_MESSAGE}
    if len(articles) > MAX_ARTICLES:
        return {"articles": f"Informe no máximo {MAX_ARTICLES} artigos."}
    current_year = timezone.localdate().year
    for article in articles:
        message = _article_error(article, current_year)
        if message:
            return {"articles": message}
    return {}


def validate_unknown_fields(
    extra_fields: Iterable[str], read_only_fields: Collection[str]
) -> dict[str, str]:
    return {
        field: (
            "Este campo não pode ser alterado."
            if field in read_only_fields
            else "Campo não reconhecido."
        )
        for field in extra_fields
    }
