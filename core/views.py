from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from accounts.mixins import ApprovedRequiredMixin
from accounts.models import User
from .forms import AssignmentForm, GACreateForm, QuestionFormSet, SolutionForm
from .markdown_util import render_markdown
from .models import Assignment, Participation, AssignmentSlot, Solution, Validation, Question
from .services.assignment import assign_randomly


def home(request):
    user = request.user
    ctx = {}
    if user.is_authenticated and getattr(user, 'is_approved', False):
        is_admin = _is_admin(user)
        ctx['is_admin'] = is_admin
        gas = []
        opted = set()
        slot_counts = {}
        submitted_counts = {}
        if not is_admin:
            opted = set(Participation.objects.filter(user=user).values_list('assignment_id', flat=True))
            slot_counts = dict(
                AssignmentSlot.objects.filter(user=user)
                .values_list('assignment_id')
                .annotate(n=Count('id'))
            )
            submitted_counts = dict(
                Solution.objects.filter(solver=user, submitted_at__isnull=False)
                .values_list('assignment_id')
                .annotate(n=Count('id'))
            )
            ctx['has_opted'] = bool(opted)
        for ga in Assignment.objects.all():
            gas.append({
                'ga': ga,
                'poll_open': ga.status == ga.Status.POLLING and not ga.is_poll_expired(),
                'is_opted': ga.id in opted,
                'my_slots': slot_counts.get(ga.id, 0),
                'submitted': submitted_counts.get(ga.id, 0),
            })
        ctx['gas'] = gas
        if is_admin:
            _build_admin_dashboard(ctx, gas)
        else:
            rev_counts = dict(
                Validation.objects.filter(reviewer=user)
                .values_list('solution__assignment_id')
                .annotate(n=Count('id'))
            )
            marks_low = {}
            needs_fix_home = {}
            for sol in Solution.objects.filter(solver=user, submitted_at__isnull=False).select_related('question'):
                if sol.status == Solution.Status.NEEDS_FIX:
                    needs_fix_home.setdefault(sol.assignment_id, []).append(sol.question.number)
                elif sol.verifier_count < 2:
                    marks_low.setdefault(sol.assignment_id, []).append(sol.question.number)
            ctx['open_gas'] = [it for it in gas if it['ga'].status != Assignment.Status.CLOSED]
            ctx['closed_gas'] = [it for it in gas if it['ga'].status == Assignment.Status.CLOSED]
            steps = []
            for it in gas:
                ga = it['ga']
                if ga.status == Assignment.Status.POLLING and it['poll_open'] and not it['is_opted']:
                    steps.append({'text': f"Polling started for {ga.title} — opt in to participate.",
                                  'url': reverse('core:ga_poll', args=[ga.slug])})
                elif ga.status == Assignment.Status.ASSIGNED:
                    if it['my_slots']:
                        if it['submitted'] < it['my_slots']:
                            qnums = sorted(AssignmentSlot.objects.filter(assignment=ga, user=user)
                                           .values_list('question__number', flat=True))
                            label = ', '.join(f'Q{n}' for n in qnums) or f"{it['my_slots']} question{'s' if it['my_slots'] != 1 else ''}"
                            steps.append({'text': f"{label} assigned to you in {ga.title} — submit solutions.",
                                          'url': reverse('core:ga_mine', args=[ga.slug])})
                        elif rev_counts.get(ga.id, 0) < 2:
                            steps.append({'text': f"All your solutions submitted for {ga.title} — review at least 2 others.",
                                          'url': reverse('core:ga_review', args=[ga.slug])})
                    elif it['is_opted']:
                        steps.append({'text': f"You weren't assigned any questions in {ga.title} — nothing to do there for now.",
                                      'url': None})
            ctx['steps'] = steps
            ctx['pending_verify'] = [
                {'title': it['ga'].title, 'qnums': sorted(marks_low[it['ga'].id])}
                for it in gas
                if it['ga'].status == Assignment.Status.ASSIGNED
                and it['ga'].id in marks_low
            ]
            ctx['needs_fix_home'] = [
                {'title': it['ga'].title, 'qnums': sorted(needs_fix_home[it['ga'].id]), 'slug': it['ga'].slug}
                for it in gas
                if it['ga'].status == Assignment.Status.ASSIGNED
                and it['ga'].id in needs_fix_home
            ]
    return render(request, 'core/home.html', ctx)


def _students_qs():
    return User.objects.filter(is_approved=True).exclude(role=User.Role.ADMIN).exclude(is_superuser=True)


def _aggregate(qs, key_field):
    return dict(qs.values_list(key_field).annotate(n=Count('id')))


def _build_admin_dashboard(ctx, gas):
    now = timezone.now()
    eligible = _students_qs().count()
    opted_c = _aggregate(Participation.objects.all(), 'assignment_id')
    slots_c = _aggregate(AssignmentSlot.objects.all(), 'assignment_id')
    subs_c = _aggregate(Solution.objects.filter(submitted_at__isnull=False), 'assignment_id')
    ver_c = _aggregate(Solution.objects.filter(status=Solution.Status.VERIFIED), 'assignment_id')
    rev_c = _aggregate(Validation.objects.all(), 'solution__assignment_id')
    one_away_c = _aggregate(
        Solution.objects.filter(submitted_at__isnull=False, verifier_count=1), 'assignment_id'
    )
    total_q_c = _aggregate(Question.objects.all(), 'assignment_id')
    covered_q_c = dict(
        Solution.objects.filter(status=Solution.Status.VERIFIED)
        .values('assignment_id')
        .annotate(covered=Count('question_id', distinct=True))
        .values_list('assignment_id', 'covered')
    )

    pending = User.objects.filter(is_approved=False).count()
    ctx['pending_count'] = pending

    attention = []
    if pending:
        attention.append({
            'level': 'high',
            'text': f"{pending} student{'s' if pending != 1 else ''} waiting for approval.",
            'url': reverse('accounts:pending_approvals'),
            'cta': 'Review',
        })

    open_cards = []
    closed_rows = []
    cov_verified = cov_slots = 0
    cov_covered_q = cov_total_q = 0

    for it in gas:
        ga = it['ga']
        st = {
            'opted': opted_c.get(ga.id, 0),
            'eligible': eligible,
            'slots': slots_c.get(ga.id, 0),
            'submitted': subs_c.get(ga.id, 0),
            'verified': ver_c.get(ga.id, 0),
            'reviews': rev_c.get(ga.id, 0),
            'one_away': one_away_c.get(ga.id, 0),
        }
        slots = st['slots']
        total_q = total_q_c.get(ga.id, 0)
        covered_q = covered_q_c.get(ga.id, 0)
        st['coverage'] = round(100 * covered_q / total_q) if total_q else 0
        st['questions'] = total_q
        concerns = []

        def raise_attention(level, text, cta='Open', form_action=None):
            attention.append({
                'level': level,
                'text': f"{ga.title}: {text}",
                'url': reverse('core:ga_detail', args=[ga.slug]),
                'cta': cta,
                'form_action': form_action,
            })

        if ga.status == Assignment.Status.POLLING:
            if ga.is_poll_expired():
                days = max((now - ga.poll_closes_at).days, 1) if ga.poll_closes_at else 1
                if st['opted'] < 2:
                    msg = f"Poll closed {days} day{'s' if days != 1 else ''} ago but only {st['opted']} opted in — can't assign (needs 2)."
                    concerns.append({'level': 'high', 'text': msg})
                    raise_attention('high', msg)
                else:
                    msg = f"Poll closed {days} day{'s' if days != 1 else ''} ago — nobody assigned yet."
                    concerns.append({'level': 'high', 'text': msg})
                    raise_attention('high', 'poll closed, still not assigned.',
                                    cta='Assign now',
                                    form_action=reverse('core:ga_assign', args=[ga.slug]))
            elif st['opted'] == 0:
                concerns.append({'level': 'warn', 'text': 'Poll is open but nobody opted in yet.'})
                raise_attention('warn', 'poll open with 0 opt-ins.', cta='View')
            elif ga.poll_closes_at and ga.poll_closes_at - now <= timedelta(hours=24):
                concerns.append({'level': 'info', 'text': 'Poll closes within 24 hours.'})
        elif ga.status == Assignment.Status.ASSIGNED:
            age = (now - ga.assigned_at).days if ga.assigned_at else 0
            if slots and st['submitted'] == 0:
                lvl = 'high' if age >= 2 else 'warn'
                txt = 'No submissions yet.' + (f' Assigned {age} days ago.' if lvl == 'high' else '')
                concerns.append({'level': lvl, 'text': txt})
                raise_attention(lvl, txt.rstrip('.').lower() + '.')
            elif slots and st['submitted'] < slots and age >= 3:
                msg = f"Stalled — only {st['submitted']}/{slots} submitted after {age} days."
                concerns.append({'level': 'high', 'text': msg})
                raise_attention('high', f"stalled at {st['submitted']}/{slots} submissions after {age} days.")
            if st['one_away']:
                n = st['one_away']
                concerns.append({'level': 'info',
                                 'text': f"{n} solution{'s' if n != 1 else ''} one review away from verified."})
            if slots and st['submitted'] == slots and st['verified'] < slots:
                concerns.append({'level': 'info',
                                 'text': f"All submitted — {slots - st['verified']} awaiting verification."})

        for c in concerns:
            if c['level'] in ('high', 'warn') and not any(a['text'].startswith(ga.title) for a in attention):
                raise_attention(c['level'], c['text'])

        # card presentation
        show_bar = bar_pct = 0
        bar_label = ''
        if ga.status == Assignment.Status.ASSIGNED and slots:
            show_bar, bar_pct = True, st['coverage']
            bar_label = (f"{covered_q}/{total_q} questions verified · {st['verified']}/{slots} solutions · "
                         f"{st['submitted']} submitted · {st['reviews']} reviews")
            cov_verified += st['verified']
            cov_slots += slots
            cov_covered_q += covered_q
            cov_total_q += total_q
        elif ga.status == Assignment.Status.POLLING:
            show_bar = True
            bar_pct = round(100 * st['opted'] / eligible) if eligible else 0
            closes = ga.poll_closes_at.strftime('%b %d, %H:%M') if ga.poll_closes_at else ''
            bar_label = f"{st['opted']}/{eligible} opted in" + (f" · closes {closes}" if closes and not ga.is_poll_expired() else '')

        it.update({
            'stats': st,
            'concerns': concerns,
            'show_bar': bool(show_bar),
            'bar_pct': bar_pct,
            'bar_label': bar_label,
        })

        if ga.status == Assignment.Status.CLOSED:
            closed_rows.append({'ga': ga, 'coverage': st['coverage'],
                                'verified': st['verified'], 'slots': slots})
        else:
            open_cards.append(it)

    ctx['attention'] = attention
    ctx['open_cards'] = open_cards
    ctx['closed_rows'] = closed_rows
    ctx['hero'] = {
        'has': cov_total_q > 0,
        'pct': round(100 * cov_covered_q / cov_total_q) if cov_total_q else 0,
        'verified': cov_covered_q,
        'slots': cov_total_q,
    }

@login_required
def ga_list(request):
    return redirect('core:home')


@login_required
def system_stats(request):
    if not _is_admin(request.user):
        return redirect('core:home')

    opted_c = _aggregate(Participation.objects.all(), 'assignment_id')
    slots_c = _aggregate(AssignmentSlot.objects.all(), 'assignment_id')
    subs_c = _aggregate(Solution.objects.filter(submitted_at__isnull=False), 'assignment_id')
    ver_c = _aggregate(Solution.objects.filter(status=Solution.Status.VERIFIED), 'assignment_id')
    rev_c = _aggregate(Validation.objects.all(), 'solution__assignment_id')
    total_q_c = _aggregate(Question.objects.all(), 'assignment_id')
    covered_q_c = dict(
        Solution.objects.filter(status=Solution.Status.VERIFIED)
        .values('assignment_id')
        .annotate(covered=Count('question_id', distinct=True))
        .values_list('assignment_id', 'covered')
    )

    ga_rows = []
    for ga in Assignment.objects.all():
        slots = slots_c.get(ga.id, 0)
        submitted = subs_c.get(ga.id, 0)
        total_q = total_q_c.get(ga.id, 0)
        covered_q = covered_q_c.get(ga.id, 0)
        ga_rows.append({
            'ga': ga,
            'opted': opted_c.get(ga.id, 0),
            'slots': slots,
            'submitted': submitted,
            'verified': ver_c.get(ga.id, 0),
            'reviews': rev_c.get(ga.id, 0),
            'coverage': round(100 * covered_q / total_q) if total_q else 0,
        })

    optins_u = _aggregate(Participation.objects.all(), 'user_id')
    slots_u = _aggregate(AssignmentSlot.objects.all(), 'user_id')
    subs_u = _aggregate(Solution.objects.filter(submitted_at__isnull=False), 'solver_id')
    ver_u = _aggregate(Solution.objects.filter(status=Solution.Status.VERIFIED), 'solver_id')
    revs_u = _aggregate(Validation.objects.all(), 'reviewer_id')

    student_rows = []
    for u in _students_qs().order_by('display_name'):
        student_rows.append({
            'user': u,
            'optins': optins_u.get(u.id, 0),
            'slots': slots_u.get(u.id, 0),
            'submitted': subs_u.get(u.id, 0),
            'verified': ver_u.get(u.id, 0),
            'reviews': revs_u.get(u.id, 0),
        })

    pending = User.objects.filter(is_approved=False).order_by('approval_requested_at')
    return render(request, 'core/stats.html', {
        'ga_rows': ga_rows,
        'student_rows': student_rows,
        'pending': pending,
        'totals': {
            'students': len(student_rows),
            'pending': pending.count(),
            'gas': Assignment.objects.count(),
            'submitted': Solution.objects.filter(submitted_at__isnull=False).count(),
            'verified': Solution.objects.filter(status=Solution.Status.VERIFIED).count(),
            'reviews': Validation.objects.count(),
        },
    })


def _is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser or getattr(user, 'role', 'participant') == 'admin')


@transaction.atomic
@login_required
def ga_create(request):
    if not _is_admin(request.user):
        return redirect('core:home')
    n = 3
    if request.method == 'POST':
        n = int(request.POST.get('num_questions') or 0)
        form = GACreateForm(request.POST, n=n)
        if form.is_valid():
            cd = form.cleaned_data
            ga = Assignment(
                title=cd['title'],
                num_questions=cd['num_questions'],
                status=Assignment.Status.POLLING,
                created_by=request.user,
            )
            ga.poll_opened_at = timezone.now()
            ga.poll_closes_at = timezone.now() + timedelta(hours=72)
            ga.save()
            Question.objects.bulk_create([
                Question(assignment=ga, number=i, order=i, key=cd.get(f'key_{i}', ''))
                for i in range(1, cd['num_questions'] + 1)
            ])
            messages.success(request, f'GA "{ga.title}" created and poll opened (72 hours, IST).')
            return redirect('core:ga_detail', slug=ga.slug)
    elif 'num_questions' in request.GET:
        try:
            n = max(1, min(int(request.GET['num_questions']), 30))
        except (TypeError, ValueError):
            n = 3
        form = GACreateForm(initial={'num_questions': n}, n=n)
    else:
        form = GACreateForm(initial={'num_questions': n}, n=n)
    return render(request, 'core/ga_create.html', {'form': form, 'n': n, 'num_choices': range(1, 31)})


@login_required
def ga_detail(request, slug):
    ga = get_object_or_404(Assignment, slug=slug)
    is_admin = _is_admin(request.user)
    question_formset = None
    if request.method == 'POST' and is_admin and request.POST.get('action') == 'save_questions':
        question_formset = QuestionFormSet(request.POST, instance=ga, prefix='q')
        if question_formset.is_valid():
            question_formset.save()
            messages.success(request, 'Questions updated.')
            return redirect('core:ga_detail', slug=ga.slug)
        messages.error(request, 'Could not save questions — fix the errors below.')
    ctx = {
        'ga': ga,
        'is_admin': is_admin,
        'is_opted': Participation.objects.filter(assignment=ga, user=request.user).exists(),
        'poll_expired': ga.is_poll_expired(),
    }
    if is_admin:
        if question_formset is None:
            question_formset = QuestionFormSet(instance=ga, prefix='q')
        opted = list(ga.participations.select_related('user').order_by('opted_in_at'))
        ctx['question_formset'] = question_formset
        ctx['opted_users'] = [p.user for p in opted]
        ctx['not_opted'] = _students_qs().exclude(id__in=[p.user_id for p in opted])
        slots_total = ga.slots.count()
        ctx['ga_stats'] = {
            'opted': len(opted),
            'eligible': _students_qs().count(),
            'slots': slots_total,
            'solutions': ga.solutions.count(),
            'submitted': ga.solutions.filter(submitted_at__isnull=False).count(),
            'verified': ga.solutions.filter(status=Solution.Status.VERIFIED).count(),
            'reviews': Validation.objects.filter(solution__assignment=ga).count(),
            'per_question': [
                {
                    'q': q,
                    'slots': q.slots.count(),
                    'submitted': q.solutions.filter(submitted_at__isnull=False).count(),
                    'verified': q.solutions.filter(status=Solution.Status.VERIFIED).count(),
                    'needs_fix': q.solutions.filter(status=Solution.Status.NEEDS_FIX).count(),
                }
                for q in ga.questions.all()
            ],
        }
    return render(request, 'core/ga_detail.html', ctx)

@login_required
def ga_open_poll(request, slug):
    if not _is_admin(request.user):
        return redirect('core:home')
    ga = get_object_or_404(Assignment, slug=slug)
    if ga.status == ga.Status.DRAFT:
        ga.status = ga.Status.POLLING
        ga.poll_opened_at = timezone.now()
        ga.poll_closes_at = ga.poll_opened_at + timedelta(hours=72)
        ga.save(update_fields=['status', 'poll_opened_at', 'poll_closes_at'])
        messages.success(request, 'Poll opened for 72 hours (IST).')
    return redirect('core:ga_detail', slug=ga.slug)


@login_required
def ga_poll(request, slug):
    ga = get_object_or_404(Assignment, slug=slug)
    is_admin = _is_admin(request.user)
    is_opted = Participation.objects.filter(assignment=ga, user=request.user).exists()
    poll_expired = ga.is_poll_expired()
    if request.method == 'POST':
        if is_admin:
            messages.error(request, 'Admins cannot opt in.')
        elif ga.status != ga.Status.POLLING or poll_expired:
            messages.error(request, 'Poll is not open.')
        elif is_opted:
            messages.info(request, 'Already opted in.')
        else:
            Participation.objects.create(assignment=ga, user=request.user)
            is_opted = True
            messages.success(request, 'Opted in.')
    return render(request, 'core/ga_poll.html', {
        'ga': ga, 'is_opted': is_opted, 'poll_expired': poll_expired, 'is_admin': is_admin,
    })


@login_required
def ga_assign(request, slug):
    if not _is_admin(request.user):
        return redirect('core:home')
    ga = get_object_or_404(Assignment, slug=slug)
    if request.method == 'POST':
        try:
            assign_randomly(ga)
            messages.success(request, 'Assigned randomly — every opt-in seated (at least 2 solvers per question).')
        except ValueError as e:
            messages.error(request, str(e))
    return redirect('core:ga_detail', slug=ga.slug)


@login_required
def ga_close(request, slug):
    if not _is_admin(request.user):
        return redirect('core:home')
    ga = get_object_or_404(Assignment, slug=slug)
    if request.method == 'POST':
        ga.status = ga.Status.CLOSED
        ga.closed_at = timezone.now()
        ga.save(update_fields=['status', 'closed_at'])
        messages.success(request, 'Assignment closed.')
    return redirect('core:home')

@login_required
def ga_map(request, slug):
    ga = get_object_or_404(Assignment, slug=slug)
    # load slots grouped
    slots = AssignmentSlot.objects.filter(assignment=ga).select_related('user', 'question')
    q_to_solvers = {}
    for s in slots:
        qid = s.question_id
        q_to_solvers.setdefault(qid, []).append(s)
    questions = list(ga.questions.all())
    # get own slots
    my_slots = set(AssignmentSlot.objects.filter(assignment=ga, user=request.user).values_list('question_id', flat=True))
    return render(request, 'core/ga_map.html', {
        'ga': ga,
        'questions': questions,
        'q_to_solvers': q_to_solvers,
        'my_slots': my_slots,
    })

def _gating_checklist(user, ga):
    items = []
    my_slots = list(AssignmentSlot.objects.filter(assignment=ga, user=user).select_related('question'))
    if not my_slots:
        items.append({'label': 'You were not assigned any questions in this GA.', 'done': False, 'url': None})
        return items
    pending_q = []
    for s in my_slots:
        sol = Solution.objects.filter(assignment=ga, question=s.question, solver=user).first()
        if not sol or not sol.submitted_at:
            pending_q.append(s.question.number)
    if pending_q:
        items.append({'label': f"Submit your assigned solutions — pending Q{', Q'.join(str(n) for n in sorted(pending_q))}.",
                      'done': False, 'url': reverse('core:ga_mine', args=[ga.slug])})
    else:
        items.append({'label': 'All your assigned solutions submitted.', 'done': True, 'url': None})
    reviews_done = Validation.objects.filter(reviewer=user, solution__assignment=ga).count()
    if reviews_done < 2:
        items.append({'label': f'Review at least 2 other solutions ({reviews_done}/2 done).',
                      'done': False, 'url': reverse('core:ga_review', args=[ga.slug])})
    else:
        items.append({'label': f'Reviewed {reviews_done} solutions — minimum met.', 'done': True, 'url': None})
    low = []
    needs_fix = []
    for sol in Solution.objects.filter(assignment=ga, solver=user, submitted_at__isnull=False).select_related('question'):
        if sol.status == Solution.Status.NEEDS_FIX:
            needs_fix.append(sol.question.number)
        elif sol.verifier_count < 2:
            low.append(sol.question.number)
    if needs_fix:
        items.append({'label': f"Fix issues and resubmit — Q{', Q'.join(str(n) for n in sorted(needs_fix))} still needs fixes.",
                      'done': False, 'url': reverse('core:ga_mine', args=[ga.slug])})
    elif low:
        items.append({'label': f"Get 2 working marks on all your submissions — Q{', Q'.join(str(n) for n in sorted(low))} still below 2.",
                      'done': False, 'url': reverse('core:ga_mine', args=[ga.slug])})
    else:
        items.append({'label': 'All your submissions have 2 working marks.', 'done': True, 'url': None})
    return items


def _gating_ok(user, ga):
    return all(i['done'] for i in _gating_checklist(user, ga))

@login_required
def ga_mine(request, slug):
    ga = get_object_or_404(Assignment, slug=slug)
    slots = AssignmentSlot.objects.filter(assignment=ga, user=request.user).select_related('question')
    sols_qs = Solution.objects.filter(assignment=ga, solver=request.user).select_related('question').prefetch_related('validations__reviewer')
    sols = {s.question_id: s for s in sols_qs}
    needs_fix_sols = [s for s in sols_qs if s.status == Solution.Status.NEEDS_FIX]
    return render(request, 'core/ga_mine.html', {'ga': ga, 'slots': slots, 'sols': sols, 'needs_fix_sols': needs_fix_sols})


@login_required
@transaction.atomic
def ga_mine_submit(request, slug, slot_id):
    ga = get_object_or_404(Assignment, slug=slug)
    slot = get_object_or_404(AssignmentSlot, id=slot_id, assignment=ga, user=request.user)
    sol, _ = Solution.objects.get_or_create(
        assignment=ga,
        question=slot.question,
        solver=request.user,
        defaults={'content_md': ''},
    )
    if request.method == 'POST':
        form = SolutionForm(request.POST, instance=sol)
        if form.is_valid():
            sol = form.save(commit=False)
            # on submit (actual submission) reset validations if resubmitting? mark as submitted
            # if content changed from previous submitted, reset votes? track by comparing submitted? simple: if submitted before, reset
            was_submitted = sol.submitted_at is not None
            sol.submitted_at = timezone.now()
            sol.status = Solution.Status.SUBMITTED
            sol.verifier_count = 0
            sol.needs_fix_count = 0
            sol.verified_at = None
            sol.last_reviewed_at = None
            sol.save()
            if was_submitted:
                Validation.objects.filter(solution=sol).delete()
            messages.success(request, 'Solution submitted (awaiting reviews).')
            return redirect('core:ga_mine', slug=ga.slug)
    else:
        form = SolutionForm(instance=sol)
    return render(request, 'core/ga_mine_submit.html', {'ga': ga, 'slot': slot, 'form': form, 'sol': sol})


@login_required
def ga_mine_preview(request, slug, slot_id):
    ga = get_object_or_404(Assignment, slug=slug)
    get_object_or_404(AssignmentSlot, id=slot_id, assignment=ga, user=request.user)
    return JsonResponse({'html': render_markdown(request.POST.get('content', ''))})

@login_required
def ga_review(request, slug):
    ga = get_object_or_404(Assignment, slug=slug)
    my_slots = list(AssignmentSlot.objects.filter(assignment=ga, user=request.user).select_related('question'))
    submitted_qs = set(
        Solution.objects.filter(assignment=ga, solver=request.user, submitted_at__isnull=False)
        .values_list('question_id', flat=True)
    )
    locked_qnums = sorted(s.question.number for s in my_slots if s.question_id not in submitted_qs)
    locked_qids = [s.question_id for s in my_slots if s.question_id not in submitted_qs]
    reviews_done = Validation.objects.filter(reviewer=request.user, solution__assignment=ga).count()
    reviewed_ids = Validation.objects.filter(
        reviewer=request.user, solution__assignment=ga
    ).values_list('solution_id', flat=True)
    sol_rev = (Validation.objects.filter(solution=OuterRef('pk'))
               .values('solution_id').annotate(c=Count('id')).values('c'))
    q_rev = (Validation.objects.filter(solution__assignment=ga, solution__question=OuterRef('question'))
             .values('solution__question').annotate(c=Count('id')).values('c'))
    eligible = (Solution.objects.filter(assignment=ga, submitted_at__isnull=False)
                .exclude(solver=request.user)
                .exclude(question_id__in=locked_qids)
                .exclude(id__in=reviewed_ids)
                .annotate(sol_rev=Subquery(sol_rev), q_rev=Subquery(q_rev))
                .order_by('q_rev', 'sol_rev', 'id')
                .select_related('question', 'solver')[:200])
    return render(request, 'core/ga_review.html', {
        'ga': ga,
        'eligible': eligible,
        'reviews_done': reviews_done,
        'locked_qnums': locked_qnums,
        'my_qs': {s.question_id for s in my_slots},
    })


@login_required
@transaction.atomic
def ga_review_solution(request, slug, solution_id):
    ga = get_object_or_404(Assignment, slug=slug)
    sol = get_object_or_404(Solution, id=solution_id, assignment=ga)
    if sol.solver_id == request.user.id:
        messages.error(request, 'Cannot review your own solution.')
        return redirect('core:ga_review', slug=ga.slug)
    if not sol.submitted_at:
        messages.error(request, 'Solution not submitted yet.')
        return redirect('core:ga_review', slug=ga.slug)
    assigned_qs = set(
        AssignmentSlot.objects.filter(assignment=ga, user=request.user).values_list('question_id', flat=True)
    )
    submitted_qs = set(
        Solution.objects.filter(assignment=ga, solver=request.user, submitted_at__isnull=False)
        .values_list('question_id', flat=True)
    )
    if sol.question_id in assigned_qs and sol.question_id not in submitted_qs:
        messages.error(request, f"Submit your own solution to Q{sol.question.number} first — you can't review that question until then.")
        return redirect('core:ga_review', slug=ga.slug)
    if Validation.objects.filter(solution=sol, reviewer=request.user).exists():
        messages.info(request, 'Already reviewed this solution.')
        return redirect('core:ga_review', slug=ga.slug)
    if request.method == 'POST':
        is_working = request.POST.get('is_working') == 'yes'
        comment = request.POST.get('comment', '').strip()
        if not is_working and not comment:
            messages.error(request, 'Please say what went wrong — the writer needs to know what to fix.')
            return render(request, 'core/ga_review_solution.html', {'ga': ga, 'sol': sol})
        try:
            Validation.objects.create(solution=sol, reviewer=request.user, is_working=is_working, comment=comment)
        except IntegrityError:
            messages.info(request, 'Already reviewed this solution.')
            return redirect('core:ga_review', slug=ga.slug)
        sol.last_reviewed_at = timezone.now()
        if is_working:
            sol.verifier_count += 1
        else:
            sol.needs_fix_count += 1
        if sol.verifier_count >= 2:
            sol.status = Solution.Status.VERIFIED
            sol.verified_at = timezone.now()
        elif not is_working and sol.needs_fix_count >= 1:
            sol.status = Solution.Status.NEEDS_FIX
        elif sol.submitted_at:
            sol.status = Solution.Status.SUBMITTED
        sol.save(update_fields=['verifier_count', 'needs_fix_count', 'last_reviewed_at', 'status', 'verified_at'])
        messages.success(request, 'Review submitted.')
        return redirect('core:ga_review', slug=ga.slug)
    return render(request, 'core/ga_review_solution.html', {'ga': ga, 'sol': sol})

@login_required
def ga_solutions(request, slug):
    ga = get_object_or_404(Assignment, slug=slug)
    checklist = _gating_checklist(request.user, ga)
    ok = _is_admin(request.user) or all(i['done'] for i in checklist)
    verified = (
        Solution.objects.filter(assignment=ga, status=Solution.Status.VERIFIED)
        .select_related('question', 'solver')
        .order_by('question__number', 'id')
    )
    by_q = {}
    for sol in verified:
        by_q.setdefault(sol.question_id, []).append(sol)
    rows = [(q, by_q[q.id]) for q in ga.questions.all() if q.id in by_q]
    return render(request, 'core/ga_solutions.html', {'ga': ga, 'ok': ok, 'checklist': checklist, 'rows': rows})


@login_required
def ga_solution_detail(request, slug, solution_id):
    ga = get_object_or_404(Assignment, slug=slug)
    if not _is_admin(request.user) and not _gating_ok(request.user, ga):
        messages.error(request, 'Complete requirements first: submit all assigned, review >=2, all own submitted must reach >=2 working marks.')
        return redirect('core:ga_solutions', slug=ga.slug)
    sol = get_object_or_404(Solution, id=solution_id, assignment=ga)
    if sol.status != Solution.Status.VERIFIED:
        messages.error(request, 'Only verified solutions are viewable here.')
        return redirect('core:ga_solutions', slug=ga.slug)
    return render(request, 'core/ga_solution_detail.html', {'ga': ga, 'sol': sol})


def explainer(request):
    return render(request, 'core/explainer.html')
