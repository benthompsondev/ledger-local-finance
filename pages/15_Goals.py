"""Unified Goals, Savings, and True Expenses experience."""
from __future__ import annotations

from datetime import date

import streamlit as st

from utils.database import (
    get_applicable_plan,
    get_connection,
    get_goals,
    init_db,
    insert_goal,
    list_accounts,
    record_goal_contribution,
    update_goal,
)
from utils.goals import (
    GOAL_TYPES,
    PROGRESS_METHODS,
    goal_plan_summary,
    goal_progress,
)
from utils.planner import analysis_anchor
from utils.styles import inject_styles


st.set_page_config(page_title="Goals · Ledger", page_icon="🎯", layout="wide")
inject_styles()
init_db()
conn = get_connection()

st.title("Goals, Savings & True Expenses")
st.caption(
    "Give savings a purpose without assigning every dollar. Link a dedicated "
    "account when it truly represents one goal, or keep progress manual when "
    "money is mixed or held elsewhere."
)


def _account_label(account: dict) -> str:
    suffix = " · archived" if int(account.get("is_archived") or 0) else ""
    institution = (
        f" · {account['institution']}" if account.get("institution") else ""
    )
    return (
        f"{account.get('name') or 'Account'} · "
        f"{(account.get('type') or '').replace('_', ' ').title()}"
        f"{institution}{suffix}"
    )


def _link_error(goal_type: str, account: dict | None) -> str:
    if account is None:
        return "Choose an account for linked-account progress."
    is_debt = account.get("type") in {
        "credit_card", "loan", "mortgage", "other_liability",
    }
    if goal_type == "debt_payoff" and not is_debt:
        return "Debt payoff goals must link a debt account."
    if goal_type != "debt_payoff" and is_debt:
        return "Savings goals must link an asset account, not a debt account."
    return ""


accounts_all = list_accounts(conn=conn, include_archived=True)
accounts_active = [
    account for account in accounts_all
    if not int(account.get("is_archived") or 0)
]
account_by_id = {int(account["id"]): account for account in accounts_all}

plan = get_applicable_plan(analysis_anchor(conn=conn), conn=conn) or {}
plan_summary = goal_plan_summary(
    float(plan.get("savings_target") or 0), conn=conn,
)
all_goal_rows = get_goals(conn=conn, status=None)
progressed = goal_progress(all_goal_rows, conn=conn)
active_goals = [goal for goal in progressed if goal["status"] == "active"]

s1, s2, s3, s4 = st.columns(4)
s1.metric("Active goals", len(active_goals))
s2.metric(
    "Named monthly allocations",
    f"${plan_summary['goal_allocations_total']:,.0f}",
    help="Active goals marked as included in the monthly plan.",
)
s3.metric(
    "Headline savings target",
    f"${plan_summary['headline_savings_target']:,.0f}",
    help="The standing target from the applicable monthly plan.",
)
s4.metric(
    "Reserved by Safe to Spend",
    f"${plan_summary['effective_savings_target']:,.0f}",
    help=(
        "Ledger reserves the larger of the headline target or named goal "
        "allocations. It never adds them together."
    ),
)

if plan_summary["allocation_above_plan"] > 0:
    st.warning(
        f"Named goal allocations are "
        f"${plan_summary['allocation_above_plan']:,.0f} above the saved "
        "savings target. Safe to Spend reserves the larger amount. Update "
        "the monthly plan if you want the headline target to match."
    )
elif plan_summary["unallocated_savings"] > 0:
    st.caption(
        f"${plan_summary['unallocated_savings']:,.0f} of the headline savings "
        "target is intentionally unassigned. You do not have to allocate "
        "every savings dollar to a goal."
    )

if plan_summary.get("foreign_allocations"):
    currencies = ", ".join(sorted({
        row["currency"] for row in plan_summary["foreign_allocations"]
    }))
    st.info(
        f"Foreign-currency goal allocations ({currencies}) are shown on their "
        "goals but are not included in CAD Safe to Spend. Ledger will not "
        "invent an exchange rate."
    )

if st.button("Open monthly plan →", key="goals_open_plan"):
    st.switch_page("pages/12_Month_Plan.py")

st.divider()

status_filter = st.radio(
    "Show",
    ["Active", "Paused", "Completed", "All"],
    horizontal=True,
    key="goals_status_filter",
)
filter_value = status_filter.lower()
visible = (
    progressed if filter_value == "all"
    else [goal for goal in progressed if goal["status"] == filter_value]
)

if not visible:
    st.info(f"No {filter_value} goals yet.")

for goal in visible:
    currency = goal.get("currency") or "CAD"
    pct = float(goal.get("progress_pct") or 0)
    title = (
        f"{goal.get('name') or 'Goal'} · {pct * 100:.0f}% · "
        f"{goal.get('status', 'active').title()}"
    )
    with st.expander(title, expanded=len(visible) <= 2):
        st.progress(pct)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Accumulated", f"{currency} ${goal['current_amount']:,.0f}")
        c2.metric("Remaining", f"{currency} ${goal['gap']:,.0f}")
        c3.metric(
            "Planned each month",
            f"{currency} ${goal['planned_monthly_contribution']:,.0f}",
        )
        pace = goal.get("required_monthly_pace")
        c4.metric(
            "Pace needed",
            (f"{currency} ${pace:,.0f}/mo" if pace is not None else "No due date"),
        )

        source_bits = [
            goal.get("type_label") or "Goal",
            goal.get("source_label") or "Manual progress",
        ]
        if goal.get("target_date"):
            source_bits.append(f"due {goal['target_date']}")
        st.caption(" · ".join(source_bits))

        if goal.get("foreign_currency"):
            st.info(
                f"This goal is measured in {currency}. Ledger does not convert "
                "it into CAD or combine it with CAD goal totals."
            )
        if goal.get("duplicate_link_warning"):
            st.warning(
                "This account is linked to more than one active goal. Ledger "
                "shows each goal separately and does not combine their progress; "
                "make sure the same balance is not being promised twice."
            )
        if goal.get("source_warning"):
            st.warning(goal["source_warning"])
        elif goal.get("data_stale"):
            st.warning(
                "The linked account has no recent activity. Import fresh data "
                "before treating this goal as on track."
            )

        if goal.get("pace_state") == "overdue":
            st.error(
                f"The target date has passed with {currency} "
                f"${goal['gap']:,.0f} remaining."
            )
        elif goal.get("pace_state") == "behind" and pace is not None:
            st.warning(
                f"The current plan is short of the required pace by "
                f"{currency} ${max(0, pace - goal['planned_monthly_contribution']):,.0f}/mo."
            )
        elif goal.get("pace_state") == "on_track":
            st.success("The planned monthly contribution matches the required pace.")

        if goal.get("next_contribution") and goal["status"] == "active":
            st.markdown(
                f"**Next useful contribution:** {currency} "
                f"${goal['next_contribution']:,.0f}"
            )

        state_copy = {
            "met": "This month's planned contribution is recorded.",
            "partial": (
                f"{currency} ${goal['contributed_this_month']:,.0f} recorded "
                "this month; the planned amount is not complete yet."
            ),
            "missed": "No contribution is recorded for this month yet.",
            "pending": "No contribution recorded yet; the month has just started.",
            "unverified": (
                "A linked balance shows progress, but Ledger does not claim a "
                "monthly contribution from balance changes alone."
            ),
        }
        if goal.get("contribution_state") in state_copy:
            st.caption(state_copy[goal["contribution_state"]])

        if (goal["progress_method"] == "manual"
                and goal["status"] == "active"
                and float(goal.get("gap") or 0) > 0):
            with st.form(f"goal_contribution_{goal['id']}"):
                default_amount = max(
                    0.01,
                    min(
                        float(goal.get("gap") or 0) or 0.01,
                        float(goal.get("next_contribution") or 0) or 0.01,
                    ),
                )
                amount = st.number_input(
                    "Record a contribution",
                    min_value=0.01,
                    value=float(default_amount),
                    step=25.0,
                    key=f"goal_contribution_amount_{goal['id']}",
                )
                contributed_on = st.date_input(
                    "Contribution date",
                    value=date.today(),
                    key=f"goal_contribution_date_{goal['id']}",
                )
                contribution_notes = st.text_input(
                    "Note (optional)",
                    key=f"goal_contribution_notes_{goal['id']}",
                )
                if st.form_submit_button("Record contribution"):
                    try:
                        record_goal_contribution(
                            int(goal["id"]), float(amount),
                            contributed_on.isoformat(), contribution_notes,
                            conn=conn,
                        )
                        conn.commit()
                        st.success("Contribution recorded.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

        with st.expander("Edit goal"):
            with st.form(f"edit_goal_{goal['id']}"):
                type_keys = list(GOAL_TYPES)
                type_index = type_keys.index(goal["type"])
                edit_type = st.selectbox(
                    "Goal type", type_keys, index=type_index,
                    format_func=lambda key: GOAL_TYPES[key],
                    key=f"edit_goal_type_{goal['id']}",
                )
                edit_target = st.number_input(
                    "Target amount", min_value=0.01,
                    value=max(0.01, float(goal["target_amount"])),
                    step=100.0, key=f"edit_goal_target_{goal['id']}",
                )
                edit_date = st.date_input(
                    "Target date (optional)",
                    value=(date.fromisoformat(goal["target_date"])
                           if goal.get("target_date") else None),
                    key=f"edit_goal_date_{goal['id']}",
                )
                edit_monthly = st.number_input(
                    "Planned monthly contribution", min_value=0.0,
                    value=float(goal["planned_monthly_contribution"]),
                    step=25.0, key=f"edit_goal_monthly_{goal['id']}",
                )
                method_keys = ["manual", "linked_account"]
                current_method = (
                    goal["progress_method"]
                    if goal["progress_method"] in method_keys else "manual"
                )
                edit_method = st.radio(
                    "Progress source", method_keys,
                    index=method_keys.index(current_method),
                    format_func=lambda key: PROGRESS_METHODS[key],
                    horizontal=True, key=f"edit_goal_method_{goal['id']}",
                )
                account_options = [None] + [int(a["id"]) for a in accounts_all]
                current_ref = goal.get("linked_account_ref")
                account_index = (
                    account_options.index(int(current_ref))
                    if current_ref is not None
                    and int(current_ref) in account_options else 0
                )
                edit_account = st.selectbox(
                    "Linked account", account_options, index=account_index,
                    format_func=lambda value: (
                        "— choose an account —" if value is None
                        else _account_label(account_by_id[value])
                    ),
                    key=f"edit_goal_account_{goal['id']}",
                )
                edit_manual = st.number_input(
                    "Manual progress amount", min_value=0.0,
                    value=float(goal.get("current_amount") or 0),
                    step=25.0, key=f"edit_goal_manual_{goal['id']}",
                    disabled=edit_method != "manual",
                )
                edit_include = st.checkbox(
                    "Include this monthly contribution in the plan",
                    value=bool(goal.get("include_in_plan", 1)),
                    key=f"edit_goal_include_{goal['id']}",
                )
                edit_notes = st.text_area(
                    "Notes", value=goal.get("notes") or "",
                    key=f"edit_goal_notes_{goal['id']}",
                )
                if st.form_submit_button("Save goal changes"):
                    account = (
                        account_by_id.get(int(edit_account))
                        if edit_account is not None else None
                    )
                    link_error = (
                        _link_error(edit_type, account)
                        if edit_method == "linked_account" else ""
                    )
                    if link_error:
                        st.error(link_error)
                    else:
                        update_goal(int(goal["id"]), {
                            "type": edit_type,
                            "target_amount": float(edit_target),
                            "target_date": (edit_date.isoformat()
                                            if edit_date else None),
                            "planned_monthly_contribution": float(edit_monthly),
                            "progress_method": edit_method,
                            "linked_account_ref": (int(edit_account)
                                                   if edit_method == "linked_account"
                                                   else None),
                            "current_amount": (float(edit_manual)
                                               if edit_method == "manual"
                                               else float(goal.get("current_amount") or 0)),
                            "currency": ((account or {}).get("currency") or "CAD"),
                            "include_in_plan": 1 if edit_include else 0,
                            "notes": edit_notes.strip(),
                            "linked_metric": None,
                        }, conn=conn)
                        conn.commit()
                        st.success("Goal updated.")
                        st.rerun()

        b1, b2 = st.columns(2)
        if goal["status"] == "active":
            if b1.button("Pause", key=f"pause_goal_{goal['id']}"):
                update_goal(goal["id"], {"status": "paused"}, conn=conn)
                conn.commit()
                st.rerun()
            if b2.button("Mark completed", key=f"complete_goal_{goal['id']}"):
                update_goal(goal["id"], {
                    "status": "completed",
                    "completed_at": date.today().isoformat(),
                }, conn=conn)
                conn.commit()
                st.rerun()
        elif goal["status"] == "paused":
            if b1.button("Resume", key=f"resume_goal_{goal['id']}"):
                update_goal(goal["id"], {"status": "active"}, conn=conn)
                conn.commit()
                st.rerun()

st.divider()
st.subheader("Create a goal")

with st.form("create_goal_form"):
    new_name = st.text_input("Name*", placeholder="e.g. Annual insurance")
    new_type = st.selectbox(
        "Type", list(GOAL_TYPES), format_func=lambda key: GOAL_TYPES[key],
    )
    new_target = st.number_input(
        "Target amount*", min_value=0.0, value=0.0, step=100.0,
        help=("For a linked debt payoff, enter the starting balance you want "
              "to eliminate. Ledger shows progress as that target minus the "
              "current balance owed."),
    )
    new_date = st.date_input("Target date (optional)", value=None)
    new_monthly = st.number_input(
        "Planned monthly contribution", min_value=0.0, value=0.0, step=25.0,
    )
    new_method = st.radio(
        "Progress source", ["manual", "linked_account"],
        format_func=lambda key: PROGRESS_METHODS[key], horizontal=True,
    )
    new_account = st.selectbox(
        "Linked account (required only for linked progress)",
        [None] + [int(a["id"]) for a in accounts_active],
        format_func=lambda value: (
            "— choose an account —" if value is None
            else _account_label(account_by_id[value])
        ),
    )
    new_manual = st.number_input(
        "Current manual progress", min_value=0.0, value=0.0, step=25.0,
        disabled=new_method != "manual",
    )
    new_include = st.checkbox(
        "Include this monthly contribution in the plan", value=True,
    )
    new_notes = st.text_area("Notes")
    submitted = st.form_submit_button("Create goal")

if submitted:
    account = (
        account_by_id.get(int(new_account))
        if new_account is not None else None
    )
    link_error = (
        _link_error(new_type, account)
        if new_method == "linked_account" else ""
    )
    if not new_name.strip():
        st.error("Name is required.")
    elif new_target <= 0:
        st.error("Target amount must be greater than zero.")
    elif link_error:
        st.error(link_error)
    else:
        insert_goal({
            "name": new_name.strip(),
            "type": new_type,
            "target_amount": float(new_target),
            "current_amount": (float(new_manual)
                               if new_method == "manual" else 0.0),
            "target_date": new_date.isoformat() if new_date else None,
            "status": "active",
            "notes": new_notes.strip(),
            "linked_metric": None,
            "progress_method": new_method,
            "linked_account_ref": (int(new_account)
                                   if new_method == "linked_account" else None),
            "planned_monthly_contribution": float(new_monthly),
            "include_in_plan": 1 if new_include else 0,
            "currency": ((account or {}).get("currency") or "CAD"),
        }, conn=conn)
        conn.commit()
        st.success("Goal created.")
        st.rerun()

conn.close()
