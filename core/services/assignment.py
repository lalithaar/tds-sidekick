import random
from collections import defaultdict
from django.db import transaction
from django.utils import timezone

from core.models import AssignmentSlot


def assign_randomly(assignment):
    questions = list(assignment.questions.all().order_by('number'))
    willing_users = list(assignment.participations.select_related('user').values_list('user_id', flat=True))
    willing_users = list(willing_users)
    n_q = len(questions)
    k_u = len(willing_users)

    if k_u < 2:
        raise ValueError('Not enough people opted in (need >= 2).')
    if n_q == 0:
        raise ValueError('No questions added to assignment.')

    random.shuffle(willing_users)
    random.shuffle(questions)

    user_to_qs = defaultdict(set)
    q_to_users = defaultdict(set)

    # Pass 1
    for q in questions:
        candidates = sorted(willing_users, key=lambda uid: len(user_to_qs[uid]))
        chosen = candidates[0]
        user_to_qs[chosen].add(q.id)
        q_to_users[q.id].add(chosen)

    # Pass 2
    for q in questions:
        while len(q_to_users[q.id]) < 2:
            candidates = sorted(willing_users, key=lambda uid: len(user_to_qs[uid]))
            for c in candidates:
                if c not in q_to_users[q.id]:
                    user_to_qs[c].add(q.id)
                    q_to_users[q.id].add(c)
                    break

    # Pass 3: seat everyone who opted in, even if it pushes a question past 2.
    # "At least 2 solvers per question" is the target; more is fine.
    for uid in willing_users:
        if user_to_qs[uid]:
            continue
        q = min(questions, key=lambda q: len(q_to_users[q.id]))
        user_to_qs[uid].add(q.id)
        q_to_users[q.id].add(uid)

    with transaction.atomic():
        AssignmentSlot.objects.filter(assignment=assignment).delete()
        slots = []
        for q in questions:
            for uid in q_to_users[q.id]:
                slots.append(AssignmentSlot(assignment=assignment, user_id=uid, question=q))
        AssignmentSlot.objects.bulk_create(slots)

    assignment.status = assignment.Status.ASSIGNED
    assignment.assigned_at = timezone.now()
    assignment.save(update_fields=['status', 'assigned_at'])
    return len(slots)
