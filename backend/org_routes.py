"""Org-admin console API — Task 6.1.

Routes live under ``/api/org/{org_id}/…`` and are gated fail-closed by
``org_admin_ctx``: the caller must be a platform admin, OR carry the
``org_admin`` flag AND belong to that exact organization (an org-admin of
org A can never touch org B → 403). A softer ``org_member_ctx`` gate exists
only for the ownership-transfer ACCEPT step, which is performed by the
successor (who is not yet an org admin).

Trial reads return aggregates + masked subjects only (SUBJ-xxx + initials) —
no raw patient PII ever leaves these endpoints. Every mutation is audited.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from starlette.concurrency import run_in_threadpool

import otp_service
from server import (
    INVITE_TTL_DAYS,
    Role,
    _invitation_status,
    _invite_link,
    new_invite_code,
    current_user,
    db,
    iso,
    now,
    serialize,
    write_audit,
)

router = APIRouter(prefix='/api/org')
# access-requests live on the trial resource per the audit doc (§CONSOLIDATED):
# POST /api/trials/{id}/access-requests (+grant)
trial_access_router = APIRouter(prefix='/api/trials')


# ── Guards ───────────────────────────────────────────────────────────────────
async def _get_org_or_404(org_id: str) -> dict:
    org = await db.organizations.find_one({'id': org_id}, {'_id': 0})
    if not org:
        raise HTTPException(404, 'Organization not found')
    return org


def _same_org(user: dict, org: dict) -> bool:
    return (user.get('organization') or '').strip() == org['name']


async def org_admin_ctx(org_id: str, user=Depends(current_user)) -> dict:
    """Fail-closed org-admin gate: platform admin passes; otherwise the caller
    must hold the org_admin flag AND be a member of THIS org."""
    org = await _get_org_or_404(org_id)
    if user['role'] == 'admin':
        return {'org': org, 'user': user, 'platform_admin': True}
    if not user.get('org_admin'):
        raise HTTPException(403, 'Org-admin access required')
    if not _same_org(user, org):
        raise HTTPException(403, 'You may only administer your own organization')
    return {'org': org, 'user': user, 'platform_admin': False}


async def org_member_ctx(org_id: str, user=Depends(current_user)) -> dict:
    """Membership gate (ownership-transfer accept only): the successor is a
    member of the org but not yet its admin."""
    org = await _get_org_or_404(org_id)
    if user['role'] != 'admin' and not _same_org(user, org):
        raise HTTPException(403, 'You are not a member of this organization')
    return {'org': org, 'user': user}


USER_PROJECTION = {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0}


def _member_row(u: dict, caller_id: str) -> dict:
    status = (u.get('status') or 'active').lower()
    return {
        'id': u['id'], 'name': u.get('full_name', ''), 'email': u.get('email', ''),
        'designation': (u.get('profile') or {}).get('designation', ''),
        'role': u.get('role', ''), 'site': u.get('site', ''),
        'department': (u.get('profile') or {}).get('department', ''),
        'admin': bool(u.get('org_admin')), 'status': status,
        'you': u['id'] == caller_id,
    }


def _masked_subject(p: dict) -> dict:
    """SUBJ-xxx + initials only — org consoles never see patient PII."""
    return {
        'subject': f"SUBJ-{(p.get('id') or '')[-3:]}",
        'initials': p.get('avatar_initials', ''),
        'status': p.get('status', ''),
        'enrolled_date': p.get('enrolled_date', ''),
    }


async def _org_member_ids(org: dict) -> List[str]:
    rows = await db.users.find({'organization': org['name']}, {'_id': 0, 'id': 1}).to_list(5000)
    return [r['id'] for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# TEAM ROSTER
# ═════════════════════════════════════════════════════════════════════════════
class OrgInviteIn(BaseModel):
    email: EmailStr
    phone: Optional[str] = ''
    full_name: Optional[str] = ''
    designation: Optional[str] = ''
    role: Role = 'crc'
    site: Optional[str] = ''


class AssignSiteIn(BaseModel):
    site: str = Field(min_length=1)

ORG_TEAM_ROLES = {
    'sponsor': {'sponsor', 'cro', 'pi', 'crc'},
    'cro': {'sponsor', 'cro', 'pi', 'crc'},
    'smo': {'pi', 'crc'},
    'site': {'pi', 'crc'},
}


@router.get('/{org_id}/members')
async def org_members(ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    rows = await db.users.find({'organization': org['name']}, USER_PROJECTION) \
        .sort('created_at', 1).to_list(2000)
    members = [_member_row(u, user['id']) for u in rows]
    # pending invitations show as "invited" roster rows
    invites = await db.invitations.find({'org': org['name'], 'status': 'pending'},
                                        {'_id': 0}).to_list(500)
    for inv in invites:
        if _invitation_status(inv) != 'pending':
            continue
        members.append({
            'id': f"invite:{inv['id']}", 'name': inv.get('full_name', ''),
            'email': inv.get('email', ''), 'designation': inv.get('designation', ''),
            'role': inv.get('role', ''), 'site': inv.get('site', ''), 'department': '',
            'admin': False, 'status': 'invited', 'you': False,
        })
    return members


@router.post('/{org_id}/members/invite')
async def org_invite_member(body: OrgInviteIn, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    email = body.email.lower()
    allowed_roles = ORG_TEAM_ROLES.get((org.get('type') or '').lower(), set())
    if body.role not in allowed_roles:
        raise HTTPException(
            400,
            f'{body.role} cannot be invited to this {org.get("type") or ""} organization',
        )
    existing = await db.users.find_one({'email': email}, {'_id': 0, 'organization': 1})
    if existing and (existing.get('organization') or '').strip() == org['name']:
        raise HTTPException(400, 'This person is already a member of the organization')
    if existing:
        raise HTTPException(409, 'This email already belongs to another organization')
    pending = await db.invitations.find_one({
        'email': email, 'org_id': org['id'], 'status': 'pending'})
    if pending and _invitation_status(pending) == 'pending':
        raise HTTPException(400, 'A pending invitation already exists for this email')
    token = new_invite_code()
    doc = {
        'id': str(uuid.uuid4()), 'token': token, 'email': email,
        'phone': (body.phone or '').strip(),
        'full_name': body.full_name or '', 'designation': body.designation or '',
        'role': body.role, 'trial_id': None, 'invited_by': user['id'],
        'org': org['name'], 'org_id': org['id'], 'site': (body.site or '').strip(),
        'inviter_name': user.get('full_name') or '',
        'inviter_organization': org['name'],
        'status': 'pending', 'created_at': now(),
        'expires_at': now() + timedelta(days=INVITE_TTL_DAYS), 'resend_count': 0,
    }
    await db.invitations.insert_one(doc)
    try:
        await run_in_threadpool(
            otp_service.send_invitation_email,
            email,
            _invite_link(token),
            doc['full_name'],
            doc['inviter_name'],
            doc['inviter_organization'],
        )
    except (otp_service.OTPConfigError, otp_service.OTPDeliveryError):
        await db.invitations.delete_one({'id': doc['id']})
        raise HTTPException(502, 'The invitation email could not be delivered.')
    await write_audit(user, 'org.member_invite',
                      f"Invited {email} to {org['name']} as {body.role}",
                      target_id=doc['id'], org_id=org['id'])
    return {**serialize(doc), 'invite_link': _invite_link(token)}


@router.delete('/{org_id}/members/{member_id}')
async def org_remove_member(member_id: str, ctx=Depends(org_admin_ctx)):
    """Deactivate a roster member (record retained — regulated app)."""
    org, user = ctx['org'], ctx['user']
    if member_id == user['id']:
        raise HTTPException(400, 'You cannot remove yourself — transfer ownership instead')
    member = await db.users.find_one({'id': member_id}, USER_PROJECTION)
    if not member or (member.get('organization') or '').strip() != org['name']:
        raise HTTPException(404, 'Member not found in this organization')
    await db.users.update_one({'id': member_id}, {'$set': {
        'status': 'Deactivated', 'org_admin': False, 'deactivated_at': now(),
        'deactivated_by': user['id']}})
    await write_audit(user, 'org.member_remove',
                      f"Deactivated {member.get('email')} in {org['name']}",
                      target_id=member_id, org_id=org['id'])
    return {'ok': True, 'id': member_id, 'status': 'deactivated'}


@router.post('/{org_id}/members/{member_id}/make-admin')
async def org_make_admin(member_id: str, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    member = await db.users.find_one({'id': member_id}, USER_PROJECTION)
    if not member or (member.get('organization') or '').strip() != org['name']:
        raise HTTPException(404, 'Member not found in this organization')
    if member.get('role') == 'patient':
        raise HTTPException(400, 'A patient account cannot administer an organization')
    await db.users.update_one({'id': member_id}, {'$set': {'org_admin': True}})
    await write_audit(user, 'org.member_make_admin',
                      f"Granted org-admin to {member.get('email')} in {org['name']}",
                      target_id=member_id, org_id=org['id'])
    return {'ok': True, 'id': member_id, 'admin': True}


@router.post('/{org_id}/members/{member_id}/assign-site')
async def org_assign_site(member_id: str, body: AssignSiteIn, ctx=Depends(org_admin_ctx)):
    """Cross-site staff assignment (SMO hospital networks)."""
    org, user = ctx['org'], ctx['user']
    member = await db.users.find_one({'id': member_id}, USER_PROJECTION)
    if not member or (member.get('organization') or '').strip() != org['name']:
        raise HTTPException(404, 'Member not found in this organization')
    site_name = body.site.strip()
    await db.users.update_one({'id': member_id}, {'$set': {'site': site_name}})
    await write_audit(user, 'org.member_assign_site',
                      f"Assigned {member.get('email')} to site \"{site_name}\"",
                      target_id=member_id, org_id=org['id'])
    return {'ok': True, 'id': member_id, 'site': site_name}


# ═════════════════════════════════════════════════════════════════════════════
# OWNERSHIP TRANSFER (admin → successor, acceptance required)
# ═════════════════════════════════════════════════════════════════════════════
class OwnershipTransferIn(BaseModel):
    successor_id: str
    reason: str = Field(min_length=10)
    handover: Literal['deactivate', 'remove'] = 'deactivate'


class OwnershipTransferDeclineIn(BaseModel):
    reason: Optional[str] = Field('', max_length=1000)


@router.post('/{org_id}/ownership-transfer')
async def org_start_ownership_transfer(body: OwnershipTransferIn, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    if body.successor_id == user['id']:
        raise HTTPException(400, 'You already administer this organization')
    successor = await db.users.find_one({'id': body.successor_id}, USER_PROJECTION)
    if not successor or (successor.get('organization') or '').strip() != org['name']:
        raise HTTPException(404, 'Successor not found in this organization')
    if successor.get('role') == 'patient':
        raise HTTPException(400, 'A patient account cannot receive ownership')
    pending = await db.ownership_transfers.find_one(
        {'org_id': org['id'], 'status': 'pending'})
    if pending:
        raise HTTPException(400, 'An ownership transfer is already pending for this organization')
    doc = {
        'id': str(uuid.uuid4()), 'org_id': org['id'], 'org_name': org['name'],
        'from_user': user['id'], 'from_name': user.get('full_name', ''),
        'to_user': successor['id'], 'to_name': successor.get('full_name', ''),
        'reason': body.reason, 'handover': body.handover,
        'status': 'pending', 'created_at': now(),
    }
    await db.ownership_transfers.insert_one(doc)
    await db.notifications.insert_one({
        'id': str(uuid.uuid4()), 'user_id': successor['id'],
        'title': f"Ownership transfer · {org['name']}",
        'body': f"{user.get('full_name', 'The current admin')} has asked you to take over "
                f"as the organization admin. Review the transfer to accept or decline.",
        'kind': 'ownership_transfer', 'org_id': org['id'],
        'transfer_id': doc['id'], 'read': False, 'created_at': now()})
    await write_audit(user, 'org.ownership_transfer_start',
                      f"Proposed ownership transfer of {org['name']} to "
                      f"{successor.get('email')} — {body.reason}",
                      target_id=doc['id'], org_id=org['id'])
    return serialize({**doc})


@router.get('/{org_id}/ownership-transfer/pending')
async def org_pending_ownership_transfer(ctx=Depends(org_member_ctx)):
    """Return only the signed-in successor's pending transfer for this org."""
    org, user = ctx['org'], ctx['user']
    transfer = await db.ownership_transfers.find_one({
        'org_id': org['id'], 'to_user': user['id'], 'status': 'pending',
    }, {'_id': 0}, sort=[('created_at', -1)])
    return serialize(transfer) if transfer else None


@router.post('/{org_id}/ownership-transfer/{transfer_id}/accept')
async def org_accept_ownership_transfer(transfer_id: str, ctx=Depends(org_member_ctx)):
    """Accepted by the SUCCESSOR (a member who is not yet org admin)."""
    org, user = ctx['org'], ctx['user']
    transfer = await db.ownership_transfers.find_one({'id': transfer_id}, {'_id': 0})
    if not transfer or transfer.get('org_id') != org['id']:
        raise HTTPException(404, 'Transfer not found')
    if transfer.get('status') != 'pending':
        raise HTTPException(400, f"This transfer is already {transfer.get('status')}")
    if transfer.get('to_user') != user['id']:
        raise HTTPException(403, 'Only the designated successor can accept this transfer')
    n = now()
    changed = await db.ownership_transfers.update_one(
        {'id': transfer_id, 'status': 'pending'}, {'$set': {
            'status': 'accepted', 'accepted_at': n, 'accepted_by': user['id']}})
    if not changed.modified_count:
        raise HTTPException(409, 'This transfer was already actioned')
    await db.users.update_one({'id': user['id']}, {'$set': {'org_admin': True}})
    handover = transfer.get('handover', 'deactivate')
    old_updates = {'org_admin': False}
    if handover == 'deactivate':
        old_updates.update({'status': 'Deactivated', 'deactivated_at': n})
    await db.users.update_one({'id': transfer['from_user']}, {'$set': old_updates})
    await db.notifications.insert_one({
        'id': str(uuid.uuid4()), 'user_id': transfer['from_user'],
        'title': f"Ownership transfer accepted · {org['name']}",
        'body': f"{user.get('full_name', 'The successor')} accepted organization ownership.",
        'kind': 'ownership_transfer', 'org_id': org['id'],
        'transfer_id': transfer_id, 'read': False, 'created_at': n})
    await write_audit(user, 'org.ownership_transfer_accept',
                      f"Accepted ownership of {org['name']} from {transfer.get('from_name')} "
                      f"(handover: {handover})",
                      target_id=transfer_id, org_id=org['id'])
    return {'ok': True, 'id': transfer_id, 'status': 'accepted', 'handover': handover}


@router.post('/{org_id}/ownership-transfer/{transfer_id}/decline')
async def org_decline_ownership_transfer(
        transfer_id: str, body: OwnershipTransferDeclineIn,
        ctx=Depends(org_member_ctx)):
    """Declined by the designated successor; no account privileges change."""
    org, user = ctx['org'], ctx['user']
    transfer = await db.ownership_transfers.find_one(
        {'id': transfer_id}, {'_id': 0})
    if not transfer or transfer.get('org_id') != org['id']:
        raise HTTPException(404, 'Transfer not found')
    if transfer.get('status') != 'pending':
        raise HTTPException(400, f"This transfer is already {transfer.get('status')}")
    if transfer.get('to_user') != user['id']:
        raise HTTPException(403, 'Only the designated successor can decline this transfer')
    n = now()
    changed = await db.ownership_transfers.update_one(
        {'id': transfer_id, 'status': 'pending'}, {'$set': {
            'status': 'declined', 'declined_at': n, 'declined_by': user['id'],
            'decline_reason': (body.reason or '').strip(),
        }})
    if not changed.modified_count:
        raise HTTPException(409, 'This transfer was already actioned')
    await db.notifications.insert_one({
        'id': str(uuid.uuid4()), 'user_id': transfer['from_user'],
        'title': f"Ownership transfer declined · {org['name']}",
        'body': (
            f"{user.get('full_name', 'The successor')} declined organization ownership."
            + (f" {(body.reason or '').strip()}" if (body.reason or '').strip() else '')
        ),
        'kind': 'ownership_transfer', 'org_id': org['id'],
        'transfer_id': transfer_id, 'read': False, 'created_at': n})
    await write_audit(
        user, 'org.ownership_transfer_decline',
        f"Declined ownership of {org['name']} from {transfer.get('from_name')}"
        + (f" — {(body.reason or '').strip()}" if (body.reason or '').strip() else ''),
        target_id=transfer_id, org_id=org['id'])
    return {'ok': True, 'id': transfer_id, 'status': 'declined'}


# ═════════════════════════════════════════════════════════════════════════════
# AUDIT TRAIL (org-scoped)
# ═════════════════════════════════════════════════════════════════════════════
@router.get('/{org_id}/audit-trail')
async def org_audit_trail(kind: Optional[str] = None, limit: int = 300,
                          ctx=Depends(org_admin_ctx)):
    org = ctx['org']
    member_ids = await _org_member_ids(org)
    q: Dict = {'$or': [{'org': org['name']}, {'user_id': {'$in': member_ids}},
                       {'org_id': org['id']}]}
    if kind:
        q['category'] = kind
    rows = await db.audit_logs.find(q, {'_id': 0}).sort('created_at', -1) \
        .to_list(max(1, min(limit, 1000)))
    return [{
        'id': r.get('id'), 'at': iso(r.get('created_at')), 'actor': r.get('user_name', ''),
        'action': r.get('action', ''), 'detail': r.get('detail', ''),
        'kind': r.get('category', ''), 'trial': r.get('trial_id'),
        'status': r.get('status', ''),
    } for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# SITES (SMO hospital network)
# ═════════════════════════════════════════════════════════════════════════════
class SiteIn(BaseModel):
    name: str = Field(min_length=1)
    address: Optional[str] = ''


@router.get('/{org_id}/sites')
async def org_sites(ctx=Depends(org_admin_ctx)):
    return await db.org_sites.find({'org_id': ctx['org']['id']}, {'_id': 0}) \
        .sort('name', 1).to_list(500)


@router.post('/{org_id}/sites')
async def org_add_site(body: SiteIn, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    name = body.name.strip()
    if await db.org_sites.find_one({'org_id': org['id'], 'name': name}):
        raise HTTPException(400, 'This site is already in the network')
    doc = {'id': str(uuid.uuid4()), 'org_id': org['id'], 'name': name,
           'address': body.address or '', 'created_at': now(), 'created_by': user['id']}
    await db.org_sites.insert_one(doc)
    await write_audit(user, 'org.site_add',
                      f"Added site \"{name}\" to {org['name']}",
                      target_id=doc['id'], org_id=org['id'])
    return serialize({**doc})


@router.delete('/{org_id}/sites/{site_id}')
async def org_remove_site(site_id: str, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    site = await db.org_sites.find_one({'id': site_id, 'org_id': org['id']}, {'_id': 0})
    if not site:
        raise HTTPException(404, 'Site not found in this organization')
    await db.org_sites.delete_one({'id': site_id})
    await write_audit(user, 'org.site_remove',
                      f"Removed site \"{site.get('name')}\" from {org['name']}",
                      target_id=site_id, org_id=org['id'])
    return {'ok': True, 'id': site_id}


# ═════════════════════════════════════════════════════════════════════════════
# TRIALS (access-keyed: full for created/granted, restricted = schedule-only;
# subjects ALWAYS masked — org consoles never see patient PII)
# ═════════════════════════════════════════════════════════════════════════════
async def _org_trial_ids_with_grant(org_id: str) -> set:
    rows = await db.org_trial_access.find(
        {'org_id': org_id, 'granted': True}, {'_id': 0, 'trial_id': 1}).to_list(500)
    return {r['trial_id'] for r in rows}


@router.get('/{org_id}/trials')
async def org_trials(ctx=Depends(org_admin_ctx)):
    org = ctx['org']
    member_ids = set(await _org_member_ids(org))
    granted = await _org_trial_ids_with_grant(org['id'])
    member_rows = await db.users.find(
        {'id': {'$in': list(member_ids)}}, {'_id': 0, 'id': 1, 'site': 1}
    ).to_list(5000)
    member_site = {
        row['id']: (row.get('site') or '').strip()
        for row in member_rows if (row.get('site') or '').strip()
    }

    # candidate trials: owned by the org (sponsor_name / creator) or worked by
    # its staff (a patient at this org's site), or explicitly granted.
    owned = await db.trials.find(
        {'$or': [{'sponsor_name': org['name']},
                 {'created_by': {'$in': list(member_ids)}}]}, {'_id': 0}).to_list(500)
    related_ids = set()
    async for p in db.patients.find(
            {'$or': [{'pi_id': {'$in': list(member_ids)}},
                     {'crc_id': {'$in': list(member_ids)}}]},
            {'_id': 0, 'trial_id': 1}):
        if p.get('trial_id'):
            related_ids.add(p['trial_id'])
    extra_ids = (related_ids | granted) - {t['id'] for t in owned}
    extra = await db.trials.find({'id': {'$in': list(extra_ids)}}, {'_id': 0}).to_list(500)

    out = []
    for t in owned + extra:
        full = (t.get('sponsor_name') == org['name']
                or t.get('created_by') in member_ids
                or t['id'] in granted)
        owner = (t.get('sponsor_name') == org['name']
                 or t.get('owning_organization_id') == org['id']
                 or t.get('created_by') in member_ids)
        creator = await db.users.find_one(
            {'id': t.get('created_by')},
            {'_id': 0, 'id': 1, 'full_name': 1, 'role': 1, 'organization': 1})
        creator_role = (creator or {}).get('role', '')
        creator_org = (creator or {}).get('organization', '')
        row = {
            'id': t['id'], 'title': t.get('title'), 'protocol_id': t.get('protocol_id'),
            'phase': t.get('phase'), 'condition': t.get('condition'),
            'status': t.get('status', 'active'),
            'archived': bool(t.get('archived')),
            'duration': t.get('duration'),
            'drug': t.get('drug'),
            'recruitment_status': t.get('recruitment_status'),
            'accessLevel': 'full' if full else 'restricted',
            'accessStatus': 'full' if full else 'restricted',
            'createdBy': t.get('created_by'),
            'enrolled': await db.patients.count_documents({'trial_id': t['id']}),
            'target': t.get('target_enrollment'),
            'target_enrollment': t.get('target_enrollment'),
            'sponsor': (
                t.get('sponsor_name')
                or (creator_org if creator_role == 'sponsor' else '')
            ),
            'cro': (
                t.get('cro_name')
                or (creator_org if creator_role == 'cro' else '')
            ),
            'documentCount': await db.files.count_documents({
                'scope_type': 'trial', 'scope_id': t['id']}),
            'updatedAt': iso(t.get('updated_at') or t.get('created_at')),
            'updatedBy': {
                'id': t.get('updated_by') or t.get('created_by'),
                'name': t.get('updated_by_name') or (creator or {}).get('full_name', ''),
            },
            'permissions': {
                'canViewDetails': full,
                'canEdit': bool(owner and full),
                'canArchive': bool(owner and full),
                'canManageDocuments': bool(owner and full),
                'canRequestAccess': not full,
            },
        }
        assigned_staff = await db.patients.find(
            {'trial_id': t['id']}, {'_id': 0, 'pi_id': 1, 'crc_id': 1}
        ).to_list(1000)
        pi_ids = sorted({
            patient.get('pi_id') for patient in assigned_staff
            if patient.get('pi_id')
        })
        crc_ids = sorted({
            patient.get('crc_id') for patient in assigned_staff
            if patient.get('crc_id')
        })
        clinical_ids = sorted(set(pi_ids + crc_ids))
        clinical_rows = await db.users.find(
            {'id': {'$in': clinical_ids}},
            {'_id': 0, 'id': 1, 'full_name': 1, 'organization': 1},
        ).to_list(1000) if clinical_ids else []
        clinical_map = {member['id']: member for member in clinical_rows}
        row['pis'] = [{
            'id': staff_id,
            'name': clinical_map.get(staff_id, {}).get('full_name', ''),
            'organization': clinical_map.get(staff_id, {}).get('organization', ''),
        } for staff_id in pi_ids]
        row['crcs'] = [{
            'id': staff_id,
            'name': clinical_map.get(staff_id, {}).get('full_name', ''),
            'organization': clinical_map.get(staff_id, {}).get('organization', ''),
        } for staff_id in crc_ids]
        row['sites'] = sorted({
            member_site[staff_id]
            for patient in assigned_staff
            for staff_id in (patient.get('pi_id'), patient.get('crc_id'))
            if staff_id in member_site
        })
        visits = await db.visits.find({'trial_id': t['id']}, {'_id': 0}) \
            .sort('visit_number', 1).to_list(200)
        timing_fields = (
            'visit_number', 'name', 'day_offset', 'day_end', 'source_day_label',
            'anchor_study_day', 'includes_day_zero', 'hour_offset',
            'hour_offset_basis', 'hour_end', 'relative_to',
            'relative_offset_days', 'period', 'arm_label', 'arm', 'window_days',
            'window_before', 'window_after',
        )
        row['schedule'] = [
            {field: v.get(field) for field in timing_fields if field in v}
            for v in visits
        ]
        if full:
            patients = await db.patients.find({'trial_id': t['id']}, {'_id': 0}).to_list(1000)
            row['subjects'] = [_masked_subject(p) for p in patients]
        out.append(row)
    return out


# ── Org-admin trial edit & archive (owner + full access only) ────────────────
class OrgTrialPatch(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    condition: Optional[str] = Field(None, max_length=300)
    drug: Optional[str] = Field(None, max_length=300)
    duration: Optional[str] = Field(None, max_length=120)
    target_enrollment: Optional[int] = Field(None, ge=0, le=1000000)
    recruitment_status: Optional[str] = Field(None, max_length=60)
    status: Optional[Literal['active', 'completed', 'terminated']] = None


class OrgTrialArchiveIn(BaseModel):
    archived: bool = True


async def _org_owned_full_trial(org: dict, trial_id: str) -> dict:
    """Return the trial only when the org OWNS it with FULL access — the same
    rule that computes canEdit/canArchive/canManageDocuments in org_trials."""
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    member_ids = set(await _org_member_ids(org))
    granted = await _org_trial_ids_with_grant(org['id'])
    full = (trial.get('sponsor_name') == org['name']
            or trial.get('created_by') in member_ids
            or trial['id'] in granted)
    owner = (trial.get('sponsor_name') == org['name']
             or trial.get('owning_organization_id') == org['id']
             or trial.get('created_by') in member_ids)
    if not (owner and full):
        raise HTTPException(403, 'Only the owning organization can modify this trial')
    return trial


@router.patch('/{org_id}/trials/{trial_id}')
async def org_edit_trial(trial_id: str, body: OrgTrialPatch, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    trial = await _org_owned_full_trial(org, trial_id)
    upd = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not upd:
        raise HTTPException(400, 'No changes were provided')
    if trial.get('archived'):
        raise HTTPException(409, 'Unarchive this trial before editing it')
    upd['updated_by'] = user['id']
    upd['updated_by_name'] = user.get('full_name', '')
    upd['updated_at'] = now()
    await db.trials.update_one({'id': trial_id}, {'$set': upd})
    changed = sorted(set(upd) - {'updated_by', 'updated_by_name', 'updated_at'})
    await write_audit(user, 'org.trial_edit',
                      f"Edited trial {trial.get('protocol_id') or trial_id}: "
                      f"{', '.join(changed)}",
                      target_id=trial_id, org_id=org['id'], trial_id=trial_id,
                      changes={k: upd[k] for k in changed})
    fresh = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    return serialize(fresh)


@router.post('/{org_id}/trials/{trial_id}/archive')
async def org_archive_trial(trial_id: str, body: OrgTrialArchiveIn,
                            ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    trial = await _org_owned_full_trial(org, trial_id)
    if bool(trial.get('archived')) == body.archived:
        raise HTTPException(409, 'This trial is already in that archive state')
    n = now()
    if body.archived:
        sets = {'archived': True, 'archived_at': n, 'archived_by': user['id'],
                'updated_by': user['id'], 'updated_by_name': user.get('full_name', ''),
                'updated_at': n}
        await db.trials.update_one({'id': trial_id}, {'$set': sets})
    else:
        await db.trials.update_one(
            {'id': trial_id},
            {'$set': {'archived': False, 'updated_by': user['id'],
                      'updated_by_name': user.get('full_name', ''), 'updated_at': n},
             '$unset': {'archived_at': '', 'archived_by': ''}})
    verb = 'archive' if body.archived else 'unarchive'
    await write_audit(user, f'org.trial_{verb}',
                      f"{verb.capitalize()}d trial {trial.get('protocol_id') or trial_id}",
                      target_id=trial_id, org_id=org['id'], trial_id=trial_id)
    return {'ok': True, 'id': trial_id, 'archived': body.archived}


# ═════════════════════════════════════════════════════════════════════════════
# TRIAL ACCESS REQUESTS — /api/trials/{id}/access-requests (+grant)
# ═════════════════════════════════════════════════════════════════════════════
class AccessRequestIn(BaseModel):
    org_id: str
    reason: Optional[str] = ''


class AccessRequestDecisionIn(BaseModel):
    reason: Optional[str] = Field('', max_length=1000)


async def _trial_owner_org(trial: dict) -> Optional[dict]:
    if trial.get('owning_organization_id'):
        org = await db.organizations.find_one(
            {'id': trial['owning_organization_id']}, {'_id': 0})
        if org:
            return org
    sponsor_name = (trial.get('sponsor_name') or '').strip()
    if sponsor_name:
        org = await db.organizations.find_one(
            {'name': sponsor_name}, {'_id': 0})
        if org:
            return org
    creator = await db.users.find_one(
        {'id': trial.get('created_by')}, {'_id': 0, 'organization': 1})
    creator_org = (creator or {}).get('organization', '').strip()
    return await db.organizations.find_one(
        {'name': creator_org}, {'_id': 0}) if creator_org else None


async def _can_decide_trial_access(user: dict, trial: dict) -> bool:
    if user.get('role') == 'admin':
        return True
    if not user.get('org_admin'):
        return False
    owner_org = await _trial_owner_org(trial)
    return bool(owner_org and _same_org(user, owner_org))


@router.get('/{org_id}/trial-access-requests')
async def org_trial_access_requests(
        status: Optional[str] = None, ctx=Depends(org_admin_ctx)):
    org = ctx['org']
    if status and status not in ('pending', 'granted', 'rejected'):
        raise HTTPException(400, 'Invalid access-request status')
    member_ids = await _org_member_ids(org)
    owned_trials = await db.trials.find({
        '$or': [
            {'owning_organization_id': org['id']},
            {'sponsor_name': org['name']},
            {'created_by': {'$in': member_ids}},
        ],
    }, {'_id': 0, 'id': 1, 'title': 1, 'protocol_id': 1}).to_list(1000)
    trial_map = {trial['id']: trial for trial in owned_trials}
    q: Dict = {'trial_id': {'$in': list(trial_map)}}
    if status:
        q['status'] = status
    rows = await db.trial_access_requests.find(
        q, {'_id': 0}).sort('created_at', -1).to_list(1000)
    return [serialize({
        **row,
        'trial_title': trial_map.get(row['trial_id'], {}).get('title', ''),
        'protocol_id': trial_map.get(row['trial_id'], {}).get('protocol_id', ''),
    }) for row in rows]


@trial_access_router.post('/{trial_id}/access-requests')
async def trial_request_access(trial_id: str, body: AccessRequestIn,
                               user=Depends(current_user)):
    """An org admin asks for FULL access to a trial their staff only has
    restricted (schedule-only) visibility of."""
    org = await _get_org_or_404(body.org_id)
    if user['role'] != 'admin':
        if not user.get('org_admin'):
            raise HTTPException(403, 'Org-admin access required')
        if not _same_org(user, org):
            raise HTTPException(403, 'You may only request access for your own organization')
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    pending = await db.trial_access_requests.find_one(
        {'trial_id': trial_id, 'org_id': org['id'], 'status': 'pending'})
    if pending:
        raise HTTPException(400, 'An access request is already pending')
    doc = {
        'id': str(uuid.uuid4()), 'trial_id': trial_id, 'org_id': org['id'],
        'org_name': org['name'], 'requested_by': user['id'],
        'requester_name': user.get('full_name', ''), 'reason': body.reason or '',
        'status': 'pending', 'created_at': now(),
    }
    await db.trial_access_requests.insert_one(doc)
    owner_org = await _trial_owner_org(trial)
    if owner_org:
        owner_admins = await db.users.find({
            'organization': owner_org['name'], 'org_admin': True,
            'status': {'$nin': ['Suspended', 'Deactivated', 'Removed']},
        }, {'_id': 0, 'id': 1}).to_list(100)
        if owner_admins:
            await db.notifications.insert_many([{
                'id': str(uuid.uuid4()), 'user_id': owner['id'],
                'title': f"Trial access request · {trial.get('protocol_id', trial_id)}",
                'body': f"{org['name']} requested full access to this trial.",
                'kind': 'trial_access_request', 'trial_id': trial_id,
                'request_id': doc['id'], 'read': False, 'created_at': now(),
            } for owner in owner_admins])
    await write_audit(user, 'org.trial_access_request',
                      f"Requested full access to trial {trial.get('protocol_id', trial_id)} "
                      f"for {org['name']}", target_id=doc['id'],
                      trial_id=trial_id, org_id=org['id'])
    return serialize({**doc})


@trial_access_router.post('/{trial_id}/access-requests/{request_id}/grant')
async def trial_grant_access(trial_id: str, request_id: str, user=Depends(current_user)):
    """Granted by a platform admin OR an org-admin of the trial-owning org."""
    req = await db.trial_access_requests.find_one({'id': request_id}, {'_id': 0})
    if not req or req.get('trial_id') != trial_id:
        raise HTTPException(404, 'Access request not found')
    if req.get('status') != 'pending':
        raise HTTPException(400, f"This request is already {req.get('status')}")
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_decide_trial_access(user, trial):
        raise HTTPException(403, 'Only the trial-owning organization admin '
                                 'or a platform admin can grant access')
    n = now()
    await db.org_trial_access.update_one(
        {'org_id': req['org_id'], 'trial_id': trial_id},
        {'$set': {'granted': True, 'granted_by': user['id'], 'granted_at': n},
         '$setOnInsert': {'id': str(uuid.uuid4())}},
        upsert=True)
    changed = await db.trial_access_requests.update_one(
        {'id': request_id, 'status': 'pending'}, {'$set': {
            'status': 'granted', 'granted_by': user['id'],
            'granted_by_name': user.get('full_name', ''), 'granted_at': n}})
    if not changed.modified_count:
        raise HTTPException(409, 'This request was already actioned')
    if req.get('requested_by'):
        await db.notifications.insert_one({
            'id': str(uuid.uuid4()), 'user_id': req['requested_by'],
            'title': f"Trial access granted · {trial.get('protocol_id', trial_id)}",
            'body': f"{req.get('org_name')} now has full access to this trial.",
            'kind': 'trial_access_decision', 'trial_id': trial_id,
            'request_id': request_id, 'read': False, 'created_at': n})
    await write_audit(user, 'org.trial_access_grant',
                      f"Granted {req.get('org_name')} full access to trial "
                      f"{trial.get('protocol_id', trial_id)}",
                      target_id=request_id, trial_id=trial_id, org_id=req['org_id'])
    return {'ok': True, 'id': request_id, 'status': 'granted'}


@trial_access_router.post('/{trial_id}/access-requests/{request_id}/reject')
async def trial_reject_access(
        trial_id: str, request_id: str, body: AccessRequestDecisionIn,
        user=Depends(current_user)):
    req = await db.trial_access_requests.find_one(
        {'id': request_id}, {'_id': 0})
    if not req or req.get('trial_id') != trial_id:
        raise HTTPException(404, 'Access request not found')
    if req.get('status') != 'pending':
        raise HTTPException(400, f"This request is already {req.get('status')}")
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_decide_trial_access(user, trial):
        raise HTTPException(403, 'Only the trial-owning organization admin '
                                 'or a platform admin can reject access')
    n = now()
    changed = await db.trial_access_requests.update_one(
        {'id': request_id, 'status': 'pending'}, {'$set': {
            'status': 'rejected', 'rejected_by': user['id'],
            'rejected_by_name': user.get('full_name', ''),
            'rejected_at': n, 'decision_reason': (body.reason or '').strip(),
        }})
    if not changed.modified_count:
        raise HTTPException(409, 'This request was already actioned')
    if req.get('requested_by'):
        await db.notifications.insert_one({
            'id': str(uuid.uuid4()), 'user_id': req['requested_by'],
            'title': f"Trial access declined · {trial.get('protocol_id', trial_id)}",
            'body': (
                f"Full access for {req.get('org_name')} was declined."
                + (f" {(body.reason or '').strip()}" if (body.reason or '').strip() else '')
            ),
            'kind': 'trial_access_decision', 'trial_id': trial_id,
            'request_id': request_id, 'read': False, 'created_at': n})
    await write_audit(
        user, 'org.trial_access_reject',
        f"Rejected {req.get('org_name')} full access to trial "
        f"{trial.get('protocol_id', trial_id)}"
        + (f" — {(body.reason or '').strip()}" if (body.reason or '').strip() else ''),
        target_id=request_id, trial_id=trial_id, org_id=req['org_id'])
    return {'ok': True, 'id': request_id, 'status': 'rejected'}


# ═════════════════════════════════════════════════════════════════════════════
# TRIAL-CREATION DELEGATION (gate for new/edit/delete trial in org consoles)
# ═════════════════════════════════════════════════════════════════════════════
class OrgDelegationRequestIn(BaseModel):
    reason: str = Field(min_length=10)


@router.get('/{org_id}/delegation-status')
async def org_delegation_status(ctx=Depends(org_admin_ctx)):
    org = ctx['org']
    latest = await db.org_delegation_requests.find_one(
        {'org_id': org['id']}, {'_id': 0}, sort=[('created_at', -1)])
    return {'delegated': bool(org.get('trial_creation_delegated')),
            'request': latest}


@router.post('/{org_id}/delegation-requests')
async def org_request_delegation(body: OrgDelegationRequestIn, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    if org.get('trial_creation_delegated'):
        raise HTTPException(400, 'Trial creation is already delegated to this organization')
    pending = await db.org_delegation_requests.find_one(
        {'org_id': org['id'], 'status': 'pending'})
    if pending:
        raise HTTPException(400, 'A delegation request is already pending')
    doc = {
        'id': str(uuid.uuid4()), 'org_id': org['id'], 'org_name': org['name'],
        'requested_by': user['id'], 'requester_name': user.get('full_name', ''),
        'reason': body.reason, 'status': 'pending', 'created_at': now(),
    }
    await db.org_delegation_requests.insert_one(doc)
    await write_audit(user, 'org.delegation_request',
                      f"Requested trial-creation delegation for {org['name']} — {body.reason}",
                      target_id=doc['id'], org_id=org['id'])
    return serialize({**doc})
