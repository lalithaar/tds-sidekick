from django import template
from django.utils.safestring import mark_safe

from ..markdown_util import render_markdown

register = template.Library()

CUTE_NAMES = [
    'Sir Whiskers', 'Princess Meow-Meow', 'Waffles', 'Sir Barks-A-Lot', 'Nugget',
    'Mochi', 'Biscuit', 'Pickle', 'Muffin', 'Chairman Meow', 'Taco', 'Mr Paws',
    'Potato', 'Snickers', 'Grumpy Cat', 'Noodle', 'Socks', 'Pudding', 'Banjo',
    'Wombat', 'Gizmo', 'Dobby the Cat', 'Fuzzy', 'Bongo', 'Tater Tot',
]

@register.filter
def get_item(d, key):
    if d is None:
        return []
    return d.get(key, [])

@register.filter
def cutie(user):
    if user is None or not getattr(user, 'id', None):
        return 'anonymous'
    return CUTE_NAMES[user.id % len(CUTE_NAMES)]

@register.filter(name='markdown', is_safe=True)
def md(value):
    return mark_safe(render_markdown(value))
