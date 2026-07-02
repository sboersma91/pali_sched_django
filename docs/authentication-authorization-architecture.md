# Authentication and Authorization Architecture

This document describes the current authentication and organization-authorization architecture after the Authentication and Authorization milestone.

## Overview

FlowLine uses Django's built-in authentication system for user login/logout and a project-level organization model for customer data ownership.

The current architecture is intentionally simple:

- Django's built-in `User` model remains the user account model.
- `Organization` represents a customer/account boundary.
- `OrganizationMembership` connects a user to one organization.
- Customer-owned scheduling records carry an `organization` ownership field.
- Operational views filter and modify data only within the current user's organization.

The implementation does not include roles, invitations, billing, self-service registration, or organization switching.

## Authentication

FlowLine uses Django's built-in authentication framework.

Current behavior:

- Login URL: `/login/`
- Logout URL: `/logout/`
- Successful login redirects to the operational dashboard: `home-paid`
- Logout redirects to the login page
- Application/operational views require authentication
- Anonymous users are redirected to login with a `next` parameter

The project uses Django's built-in `User` model. It does not define a custom user model.

Public views:

- Public landing page
- Login page

Protected views include operational dashboards, list pages, detail pages, create/edit/delete workflows, schedule generation, manual schedule editing endpoints, repair endpoints, and CSV export.

## Organization Model

`members.Organization` represents the customer/account boundary.

High-level fields:

- `name`
- `created_at`
- `updated_at`

`members.OrganizationMembership` connects a Django user to an organization.

High-level fields:

- `user`
- `organization`
- `created_at`
- `updated_at`

Current relationship structure:

```text
Organization
├── User A
├── User B
└── User C
```

The current implementation supports:

- Many users per organization
- One organization per user

The one-organization-per-user rule is enforced by `OrganizationMembership.user` being one-to-one. This keeps the current application behavior simple while still allowing teams of users to work inside the same organization.

## Organization Ownership

The following scheduling models are organization-owned:

- `Locations`
- `Course`
- `Schools`
- `TheSched`
- `Instructor`

Ownership is stored directly on each model with an `organization` foreign key. The relationship uses protected deletion, so an organization cannot be deleted while owned operational data still depends on it.

### Scoped Uniqueness

Formerly global uniqueness has been changed to organization-scoped uniqueness where names should be unique inside a customer account but reusable across accounts.

Current scoped uniqueness rules:

- `Locations`: `organization + loc_name`
- `Course`: `organization + course_name`
- `Course`: `organization + abriviation`, when abbreviation is not null or blank
- `Schools`: `organization + school_name`
- `TheSched`: `organization + sched_name`

This allows two organizations to have a location, activity, school, or schedule with the same display name without conflicting.

## Authorization Rules

Authorization is enforced server-side. The UI may hide links, but security does not rely on hidden links.

### Queryset Filtering

Operational list views filter records by the current user's organization.

This applies to:

- Locations
- Activities/Courses
- Schools
- Schedules

### Object Access Protection

Detail, update, delete, schedule generation, manual editing, repair, and export lookups filter by organization ownership.

Direct URL tampering against another organization's object should fail with a not-found response instead of exposing the object.

### Create Ownership Enforcement

New operational records are assigned to the current user's organization by the server.

Normal application forms do not expose organization selection to regular users. The organization is not trusted from form input.

### Edit and Delete Enforcement

Users can only edit or delete records whose `organization` matches their membership organization.

### Form Filtering

Related-object choices are scoped to the current user's organization.

Examples:

- Activity forms only show locations from the user's organization.
- School forms only show activities from the user's organization.
- Schedule forms only show schools from the user's organization.

### CSV Export Protection

Schedule CSV export checks schedule ownership before returning schedule data.

### Schedule Generation Protection

Schedule generation operates on the schedule's organization. It does not use globally aggregated activity/location lookup data.

## Schedule Generation Isolation

The scheduler historically used module-level lookup containers:

- `master_locs`
- `class_locs`
- `class_len`

These are still present for compatibility with existing scheduling code, but initialization is now organization-aware.

Current behavior:

- `master_locs` contains only available locations owned by the relevant organization.
- `class_locs` maps only organization-owned activities to organization-owned available locations.
- `class_len` contains only organization-owned activity lengths.
- Schedule generation for `TheSched` initializes lookup data using the schedule's organization.
- Generated schedule display resolves activity metadata within the schedule's organization.

The old raw SQL lookup path was replaced with organization-aware ORM queries so scheduling data is not aggregated across customer accounts.

## Current Limitations

The current architecture intentionally remains limited.

Known limitations:

- `Default Organization` fallback still exists for migration safety and legacy/test paths.
- Each user can belong to only one organization.
- There is no role hierarchy.
- There is no invitation system.
- There is no self-service registration flow.
- There is no billing or subscription system.
- There is no organization switching UI.
- Existing schedule JSON is not rewritten to include organization metadata.

Before production use, every real user should have an explicit `OrganizationMembership`.

## Future Considerations

These are possible future directions, not commitments:

- Platform-admin organization context switching for support/debugging.
- Clearer handling for authenticated users without memberships.
- Multi-organization-per-user support if a real workflow requires it.
- Explicit system-owned template catalogs if shared activities or locations become product requirements.
- Further cleanup of legacy function-based routes after usage is reviewed.

## Architecture Decision Record

### Decision

Use Django's built-in `User` model, a separate `OrganizationMembership` model, and organization-scoped ownership fields on customer data models.

### Rationale

The project is in a stabilization phase. The goal is to add basic account isolation without broad rewrites or enterprise-level permission complexity.

Django's built-in `User` model was kept because:

- It is already supported by Django authentication.
- The project does not currently need custom user fields.
- Replacing the user model after migrations exist is high-risk.

`OrganizationMembership` was added separately because:

- User identity and customer/account ownership are different concerns.
- Many users can belong to one organization.
- The structure can evolve later without replacing Django auth.

Organization-scoped ownership was added to operational models because:

- Locations, activities, schools, schedules, and instructors are customer-owned data.
- Server-side filtering needs a reliable database ownership boundary.
- Scoped uniqueness allows the same names to exist in different customer accounts.

### Alternatives Not Chosen

Custom user model:

- Not needed for the current requirements.
- Would create unnecessary migration and compatibility risk.

Complex role system:

- Out of scope for the current milestone.
- The current product only needs organization isolation, not role-based authorization.

Enterprise permission architecture:

- Too broad for the current stabilization phase.
- Would add complexity before the app has a confirmed need for roles, invitations, billing, or multi-organization switching.
