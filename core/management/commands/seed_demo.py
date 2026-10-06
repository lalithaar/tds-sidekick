import random
import secrets
import string
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from core.models import Assignment, Participation, Question, AssignmentSlot, Solution, Validation
from core.services.assignment import assign_randomly

DEMO_PASSWORD = 'DemoPass123!'

STUDENTS = [
    ('Asha Nair', '24f2100001'),
    ('Rahul Verma', '24f2100002'),
    ('Meera Iyer', '24f2100003'),
    ('Arjun Rao', '24f2100004'),
    ('Sneha Das', '24f2100005'),
    ('Vikram Shah', '24f2100006'),
]
PENDING_STUDENT = ('Chetan Kumar', '24f2100007')

DEMO_TITLES = {
    'closed': 'Demo 1: Intro Session',
    'assigned': 'Demo 2: Midterm Sprint',
    'polling': 'Demo 3: Weekend Hack',
    'draft': 'Demo 4: Final Showcase',
}

SOLUTION_MD = """### Approach
1. Parse the input and normalize the constraints.
2. Apply the greedy strategy with a fallback pass.
3. Verify the result against the sample cases.

**Complexity:** O(n log n)

```
def solve(data):
    return sorted(data)
```
"""


def _email(tag):
    return f'{tag}@ds.study.iitm.ac.in'


def _key():
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))


class Command(BaseCommand):
    help = 'Seed demo data: students, a pending signup, and GAs in every lifecycle stage (draft/polling/assigned/closed).'

    def handle(self, *args, **options):
        with transaction.atomic():
            self.cleanup()
            admin = User.objects.get(username='admin')
            students = self.create_students()
            star = students[0]
            self.create_pending_student()

            ga_closed = self.make_ga(DEMO_TITLES['closed'], 4, admin)
            ga_assigned = self.make_ga(DEMO_TITLES['assigned'], 3, admin)
            ga_polling = self.make_ga(DEMO_TITLES['polling'], 3, admin)
            self.make_ga(DEMO_TITLES['draft'], 5, admin)

            self.build_closed(ga_closed, students, star)
            self.build_assigned(ga_assigned, students)
            self.build_polling(ga_polling, students)

        self.print_summary(admin, students, star)

    # ---------- setup ----------

    def cleanup(self):
        emails = [_email(t) for _, t in STUDENTS] + [_email(PENDING_STUDENT[1])]
        User.objects.filter(email__in=emails).delete()
        Assignment.objects.filter(title__in=list(DEMO_TITLES.values())).delete()

    def create_students(self):
        now = timezone.now()
        students = []
        for name, tag in STUDENTS:
            u = User.objects.create(
                username=tag,
                email=_email(tag),
                display_name=name,
                role=User.Role.PARTICIPANT,
                password=make_password(DEMO_PASSWORD),
                is_approved=True,
                approved_at=now,
            )
            students.append(u)
        return students

    def create_pending_student(self):
        name, tag = PENDING_STUDENT
        u = User(
            username=tag,
            email=_email(tag),
            display_name=name,
            role=User.Role.PARTICIPANT,
            password=make_password(DEMO_PASSWORD),
            is_approved=False,
        )
        u.generate_approval_key()
        u.save()
        return u

    def make_ga(self, title, num_questions, admin):
        ga = Assignment.objects.create(title=title, num_questions=num_questions, created_by=admin)
        for i in range(1, num_questions + 1):
            Question.objects.create(assignment=ga, number=i, order=i, key=_key())
        return ga

    # ---------- lifecycle builders ----------

    def opt_in(self, ga, students):
        for u in students:
            Participation.objects.get_or_create(assignment=ga, user=u)

    def make_solution(self, ga, slot, submit=True):
        sol, _ = Solution.objects.get_or_create(
            assignment=ga, question=slot.question, solver=slot.user,
            defaults={'content_md': ''},
        )
        sol.content_md = SOLUTION_MD.replace('### Approach', f"### Q{slot.question.number} — {slot.user.display_name}'s approach")
        if submit:
            sol.submitted_at = timezone.now()
            sol.status = Solution.Status.SUBMITTED
        sol.save()
        return sol

    @staticmethod
    def recount(sol):
        working = sol.validations.filter(is_working=True).count()
        sol.verifier_count = working
        sol.needs_fix_count = sol.validations.filter(is_working=False).count()
        if working >= 2 and sol.submitted_at:
            sol.status = Solution.Status.VERIFIED
            sol.verified_at = sol.verified_at or timezone.now()
        elif sol.submitted_at:
            sol.status = Solution.Status.SUBMITTED
        else:
            sol.status = Solution.Status.DRAFT
        sol.save()

    def build_closed(self, ga, students, star):
        now = timezone.now()
        ga.poll_opened_at = now - timedelta(days=10)
        ga.poll_closes_at = now - timedelta(days=9)
        ga.save(update_fields=['poll_opened_at', 'poll_closes_at'])
        self.opt_in(ga, students)
        assign_randomly(ga)

        slots = list(AssignmentSlot.objects.filter(assignment=ga).select_related('user', 'question'))
        star_slots = [s for s in slots if s.user_id == star.id]
        if not star_slots:  # guarantee the star student has work to do
            star_slots = [AssignmentSlot.objects.create(assignment=ga, user=star, question=ga.questions.first())]
            slots += star_slots

        solutions = [self.make_solution(ga, s, submit=True) for s in slots]

        # two working reviews per solution; most reviews go to a small reviewer
        # pool so other students still see the "Locked" gate on Verified Solutions
        preferred = students[:3]
        for i, sol in enumerate(solutions):
            pref = [u for u in preferred if u.id != sol.solver_id]
            others = [u for u in students if u not in pref and u.id != sol.solver_id]
            start = i % max(len(pref), 1)
            rotated = pref[start:] + pref[:start]
            reviewers = []
            for cand in rotated + others:
                if cand in reviewers:
                    continue
                reviewers.append(cand)
                if len(reviewers) == 2:
                    break
            for r in reviewers:
                Validation.objects.create(solution=sol, reviewer=r, is_working=True, comment='Verified, looks good.')
            self.recount(sol)

        # star must have reviewed >= 2 so the Verified Solutions gate unlocks for them
        have = Validation.objects.filter(reviewer=star).count()
        for sol in solutions:
            if have >= 2:
                break
            if sol.solver_id == star.id or Validation.objects.filter(solution=sol, reviewer=star).exists():
                continue
            victim = sol.validations.exclude(reviewer=star).first()
            if sol.validations.count() >= 2 and victim:
                victim.delete()
            Validation.objects.create(solution=sol, reviewer=star, is_working=True, comment='Double-checked.')
            self.recount(sol)
            have += 1

        ga.assigned_at = now - timedelta(days=8)
        ga.closed_at = now - timedelta(days=7)
        ga.status = Assignment.Status.CLOSED
        ga.save(update_fields=['assigned_at', 'closed_at', 'status'])

    def build_assigned(self, ga, students):
        now = timezone.now()
        ga.poll_opened_at = now - timedelta(days=5)
        ga.poll_closes_at = now - timedelta(days=4)
        ga.save(update_fields=['poll_opened_at', 'poll_closes_at'])
        self.opt_in(ga, students)
        assign_randomly(ga)
        ga.assigned_at = now - timedelta(days=2)
        ga.save(update_fields=['assigned_at'])

        slots = list(AssignmentSlot.objects.filter(assignment=ga).select_related('user', 'question'))
        submitted = [self.make_solution(ga, s, submit=(i % 3 != 2)) for i, s in enumerate(slots)]

        # partial review activity: one "working" vote, one "needs fix"
        done = [s for s in submitted if s.submitted_at]
        if done:
            sol = done[0]
            reviewer = next(u for u in students if u.id != sol.solver_id)
            Validation.objects.create(solution=sol, reviewer=reviewer, is_working=True, comment='One more needed.')
            self.recount(sol)
        if len(done) > 1:
            sol = done[1]
            reviewer = next(u for u in students if u.id != sol.solver_id)
            Validation.objects.create(solution=sol, reviewer=reviewer, is_working=False, comment='Edge case fails.')
            self.recount(sol)

    def build_polling(self, ga, students):
        now = timezone.now()
        ga.status = Assignment.Status.POLLING
        ga.poll_opened_at = now - timedelta(days=1)
        ga.poll_closes_at = now + timedelta(days=2)
        ga.save(update_fields=['status', 'poll_opened_at', 'poll_closes_at'])
        for u in students[:4]:  # 4 of 6 opted in so far
            Participation.objects.get_or_create(assignment=ga, user=u)

    # ---------- output ----------

    def print_summary(self, admin, students, star):
        out = [
            '',
            'Demo data seeded.',
            '',
            'LOGINS (password for all demo users: %s)' % DEMO_PASSWORD,
            '  admin            %s' % admin.email,
        ]
        for u in students:
            star_mark = '  <- star (verified-solutions unlocked)' if u.id == star.id else ''
            out.append('  %-17s %s%s' % (u.username, u.email, star_mark))
        out += [
            '  %-17s %s  (pending approval, key: %s)' % (PENDING_STUDENT[1], _email(PENDING_STUDENT[1]), User.objects.get(email=_email(PENDING_STUDENT[1])).approval_key),
            '',
            'TOUR (login as admin or a student, then open / ):',
            '  %s  - draft (no poll yet)' % DEMO_TITLES['draft'],
            '  %s  - polling, 4/6 opted in (see Opt-in flow)' % DEMO_TITLES['polling'],
            '  %s  - assigned, partial submissions + reviews' % DEMO_TITLES['assigned'],
            '  %s  - closed, all verified (login as %s to unlock solutions)' % (DEMO_TITLES['closed'], students[0].username),
            '  /accounts/approvals/ - 1 pending student waiting',
            '',
            'Re-run anytime: python manage.py seed_demo  (wipes and reseeds only demo data)',
        ]
        self.stdout.write(self.style.SUCCESS('\n'.join(out)))
