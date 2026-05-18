"""Pydantic schemas for the mail rules engine.

Conditions form a recursive AND/OR tree; actions are a flat list with a
discriminated union on ``type``. The Django layer stores both as JSONField
payloads; this module is the single source of truth for valid shapes.
"""
from typing import Annotated, Any, List, Literal, Optional, Union
from uuid import UUID

from pydantic import (
    BaseModel,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    model_validator,
)


class SchemaError(ValueError):
    """Wraps a pydantic.ValidationError into a single exception type
    callers can catch without depending on pydantic."""


ALLOWED_FIELDS = {
    'from', 'to', 'cc', 'recipient', 'subject', 'body',
    'folder', 'has_attachments', 'is_starred', 'date',
}
TEXT_FIELDS = {'from', 'to', 'cc', 'recipient', 'subject', 'body', 'folder'}
BOOL_FIELDS = {'has_attachments', 'is_starred'}
DATE_FIELDS = {'date'}

TEXT_OPS = {'contains', 'equals', 'starts_with', 'ends_with', 'matches_regex', 'in_list'}
BOOL_OPS = {'is_true', 'is_false'}
DATE_OPS = {'greater_than', 'less_than'}
ALL_OPS = TEXT_OPS | BOOL_OPS | DATE_OPS


class LeafCondition(BaseModel):
    field: str
    op: str
    value: Optional[Union[str, bool, List[str]]] = None
    case_sensitive: bool = False

    @model_validator(mode='after')
    def _check(self):
        if self.field not in ALLOWED_FIELDS:
            raise ValueError(f'unknown field: {self.field}')
        if self.op not in ALL_OPS:
            raise ValueError(f'unknown op: {self.op}')
        if self.field in TEXT_FIELDS and self.op not in TEXT_OPS:
            raise ValueError(f'op {self.op!r} not valid on text field {self.field!r}')
        if self.field in BOOL_FIELDS and self.op not in BOOL_OPS:
            raise ValueError(f'op {self.op!r} not valid on boolean field {self.field!r}')
        if self.field in DATE_FIELDS and self.op not in DATE_OPS:
            raise ValueError(f'op {self.op!r} not valid on date field {self.field!r}')
        if self.op in BOOL_OPS:
            return self
        if self.op == 'in_list':
            if not isinstance(self.value, list) or not all(isinstance(v, str) for v in self.value):
                raise ValueError('in_list value must be a list of strings')
            return self
        if self.value is None:
            raise ValueError(f'op {self.op!r} requires a value')
        return self


class GroupCondition(BaseModel):
    type: Literal['all', 'any']
    conditions: List['ConditionNode'] = Field(default_factory=list, max_length=20)


def _discriminate(v: Any) -> str:
    if isinstance(v, GroupCondition):
        return 'group'
    if isinstance(v, LeafCondition):
        return 'leaf'
    if isinstance(v, dict):
        return 'group' if v.get('type') in ('all', 'any') else 'leaf'
    return 'leaf'


ConditionNode = Annotated[
    Union[
        Annotated[LeafCondition, Tag('leaf')],
        Annotated[GroupCondition, Tag('group')],
    ],
    Discriminator(_discriminate),
]

GroupCondition.model_rebuild()


def parse_conditions(payload: Any) -> Union[LeafCondition, GroupCondition]:
    """Parse a conditions JSON payload into a typed node tree.

    Raises ``SchemaError`` on any validation failure (unknown field/op,
    bad shape, max-length exceeded). Depth/size limits are enforced by
    ``validate_tree_limits`` which callers run separately so leaf-only
    payloads can skip the recursion.
    """
    from pydantic import TypeAdapter

    adapter = TypeAdapter(ConditionNode)
    try:
        return adapter.validate_python(payload)
    except ValidationError as e:
        raise SchemaError(str(e)) from e


MAX_DEPTH = 5
MAX_LEAVES = 20


def validate_tree_limits(node: Union[LeafCondition, GroupCondition]) -> None:
    """Raise ``SchemaError`` if the tree exceeds depth or leaf-count limits.

    These limits are not enforced by the Pydantic models themselves because
    Pydantic's per-list ``max_length`` already caps per-group fanout at 20.
    The recursive total-leaf and depth checks belong here so the message is
    explicit.
    """
    def _walk(n, depth):
        if depth > MAX_DEPTH:
            raise SchemaError(f'tree depth {depth} exceeds {MAX_DEPTH}')
        if isinstance(n, GroupCondition):
            for child in n.conditions:
                _walk(child, depth + 1)

    def _count(n) -> int:
        if isinstance(n, LeafCondition):
            return 1
        return sum(_count(c) for c in n.conditions)

    _walk(node, 0)
    total = _count(node)
    if total > MAX_LEAVES:
        raise SchemaError(f'too many conditions ({total} > {MAX_LEAVES})')


class _ActionBase(BaseModel):
    pass


class AddLabelAction(_ActionBase):
    type: Literal['add_label']
    label_id: UUID


class RemoveLabelAction(_ActionBase):
    type: Literal['remove_label']
    label_id: UUID


class MarkReadAction(_ActionBase):
    type: Literal['mark_read']


class MarkUnreadAction(_ActionBase):
    type: Literal['mark_unread']


class StarAction(_ActionBase):
    type: Literal['star']


class UnstarAction(_ActionBase):
    type: Literal['unstar']


class MoveToFolderAction(_ActionBase):
    type: Literal['move_to_folder']
    folder_id: UUID


class DeleteAction(_ActionBase):
    type: Literal['delete']


ActionNode = Annotated[
    Union[
        AddLabelAction, RemoveLabelAction,
        MarkReadAction, MarkUnreadAction,
        StarAction, UnstarAction,
        MoveToFolderAction, DeleteAction,
    ],
    Field(discriminator='type'),
]


def parse_actions(payload: Any) -> List[Union[
    AddLabelAction, RemoveLabelAction,
    MarkReadAction, MarkUnreadAction,
    StarAction, UnstarAction,
    MoveToFolderAction, DeleteAction,
]]:
    """Parse a list of action dicts into typed action models.

    Raises ``SchemaError`` if any item has an unknown ``type`` or is missing
    a required field. Returns ``[]`` for an empty list (a rule with no
    actions is meaningless but legal at this layer; CRUD validation rejects
    it).
    """
    from pydantic import TypeAdapter

    if not isinstance(payload, list):
        raise SchemaError('actions payload must be a list')
    adapter = TypeAdapter(List[ActionNode])
    try:
        return adapter.validate_python(payload)
    except ValidationError as e:
        raise SchemaError(str(e)) from e
