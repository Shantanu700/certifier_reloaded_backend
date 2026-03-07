from typing import List, Literal, TypeAlias, TypedDict, Union

from pydantic import BaseModel

from base.enums import RoleEnum

AllowedRolesType: TypeAlias = Union[List[RoleEnum], Literal["any"]]


class MethodTypes(TypedDict, total=False):
    get: AllowedRolesType
    post: AllowedRolesType
    put: AllowedRolesType
    delete: AllowedRolesType


class AuthenticationMethodTypes(TypedDict, total=False):
    get: bool
    post: bool
    put: bool
    delete: bool


class DynamicKeysType(TypedDict):
    name: str
    source: str
    type: Literal["int", "str", "float", "bool"]


class CascaderType(TypedDict):
    name: str
    field: str


class RecursiveType(CascaderType):
    index: int


class PaginationConfigType(TypedDict):
    run_before_pagination: str
    run_after_pagination: str


class FileFieldType(TypedDict):
    url: str
    uuid: str


class UnAuthenticatedResponseType(BaseModel):
    msg: Literal["You are unauthenticated! Please login again!"]


class UnAuthorizedResponseType(BaseModel):
    msg: Literal["You are not authorized to access this page!"]


class CustomErrorResponseType(BaseModel):
    msg: str


class SerializerErrorResponseType(BaseModel):
    detail: list[str]


class DefaultSuccessResponseType(BaseModel):
    msg: str
