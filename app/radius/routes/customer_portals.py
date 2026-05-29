"""Self-scoped subscriber and card user portal routes."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusValidationError
from ..services.customer_portals import CustomerPortalService, PortalAuthError


def register_customer_portal_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/customer-portals", "customer_portals_admin", customer_portals_admin, methods=["GET"])
    bp.add_url_rule("/portal/subscriber/login", "portal_subscriber_login", subscriber_login, methods=["GET", "POST"])
    bp.add_url_rule("/portal/subscriber/logout", "portal_subscriber_logout", subscriber_logout, methods=["GET", "POST"])
    bp.add_url_rule("/portal/subscriber", "portal_subscriber_home", subscriber_home, methods=["GET"])
    bp.add_url_rule("/portal/subscriber/loan-request", "portal_subscriber_loan_request", subscriber_loan_request, methods=["POST"])
    bp.add_url_rule("/portal/subscriber/renewal-request", "portal_subscriber_renewal_request", subscriber_renewal_request, methods=["POST"])
    bp.add_url_rule("/portal/card/login", "portal_card_login", card_login, methods=["GET", "POST"])
    bp.add_url_rule("/portal/card/logout", "portal_card_logout", card_logout, methods=["GET", "POST"])
    bp.add_url_rule("/portal/card", "portal_card_home", card_home, methods=["GET"])
    bp.add_url_rule("/portal/card/purchase", "portal_card_purchase", card_purchase, methods=["POST"])
    bp.add_url_rule("/portal/card/redeem", "portal_card_redeem", card_redeem, methods=["POST"])


def _tenant_id() -> int:
    return int(session.get("portal_tenant_id") or 1)


def _svc() -> CustomerPortalService:
    return CustomerPortalService(tenant_id=_tenant_id())


def customer_portals_admin():
    return render_template("radius/customer_portals_admin.html")


def subscriber_login():
    if request.method == "POST":
        try:
            subscriber = CustomerPortalService(tenant_id=1).authenticate_subscriber(
                username=request.form.get("username") or "",
                password=request.form.get("password") or "",
            )
        except PortalAuthError:
            flash("Subscriber credentials are not valid.", "error")
            return render_template("radius/portal_subscriber_login.html"), 401
        session["portal_tenant_id"] = 1
        session["portal_subscriber_id"] = int(subscriber["id"])
        session.pop("portal_card_user_id", None)
        return redirect(url_for("radius.portal_subscriber_home"))
    return render_template("radius/portal_subscriber_login.html")


def subscriber_logout():
    session.pop("portal_subscriber_id", None)
    flash("Subscriber portal signed out.", "info")
    return redirect(url_for("radius.portal_subscriber_login"))


def subscriber_home():
    subscriber_id = session.get("portal_subscriber_id")
    if not subscriber_id:
        return redirect(url_for("radius.portal_subscriber_login"))
    dashboard = _svc().subscriber_dashboard(int(subscriber_id))
    return render_template("radius/portal_subscriber.html", dashboard=dashboard)


def subscriber_loan_request():
    subscriber_id = session.get("portal_subscriber_id")
    if not subscriber_id:
        return redirect(url_for("radius.portal_subscriber_login"))
    try:
        result = _svc().submit_loan_request(
            subscriber_id=int(subscriber_id),
            requested_minutes=int(request.form.get("requested_minutes") or 0),
            reason=request.form.get("reason") or "",
        )
        flash(f"Loan request saved: {result['status']}", "success")
    except (RadiusValidationError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.portal_subscriber_home"))


def subscriber_renewal_request():
    subscriber_id = session.get("portal_subscriber_id")
    if not subscriber_id:
        return redirect(url_for("radius.portal_subscriber_login"))
    result = _svc().submit_renewal_request(
        subscriber_id=int(subscriber_id),
        reason=request.form.get("reason") or "",
    )
    flash(f"Renewal request saved: {result['status']}", "success")
    return redirect(url_for("radius.portal_subscriber_home"))


def card_login():
    if request.method == "POST":
        try:
            card_user = CustomerPortalService(tenant_id=1).authenticate_card_user(
                mobile=request.form.get("mobile") or "",
                password=request.form.get("password") or "",
            )
        except PortalAuthError:
            flash("رقم الجوال أو كلمة المرور غير صحيحة.", "error")
            return render_template("radius/portal_card_login.html"), 401
        session["portal_tenant_id"] = 1
        session["portal_card_user_id"] = int(card_user["id"])
        session.pop("portal_subscriber_id", None)
        return redirect(url_for("radius.portal_card_home"))
    return render_template("radius/portal_card_login.html")


def card_logout():
    session.pop("portal_card_user_id", None)
    flash("Card portal signed out.", "info")
    return redirect(url_for("radius.portal_card_login"))


def card_home():
    card_user_id = session.get("portal_card_user_id")
    if not card_user_id:
        return redirect(url_for("radius.portal_card_login"))
    dashboard = _svc().card_user_dashboard(int(card_user_id))
    return render_template("radius/portal_card.html", dashboard=dashboard)


def card_purchase():
    card_user_id = session.get("portal_card_user_id")
    if not card_user_id:
        return redirect(url_for("radius.portal_card_login"))
    try:
        purchase = _svc().purchase_card_package(
            card_user_id=int(card_user_id),
            package_id=int(request.form.get("package_id") or 0),
        )
        flash(f"Card purchased: #{purchase['id']}", "success")
    except (ValueError, RadiusValidationError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.portal_card_home"))


def card_redeem():
    card_user_id = session.get("portal_card_user_id")
    if not card_user_id:
        return redirect(url_for("radius.portal_card_login"))
    try:
        result = _svc().redeem_card_to_wallet(
            card_user_id=int(card_user_id),
            card_number=request.form.get("card_number") or "",
            card_password=request.form.get("card_password") or "",
        )
        flash(f"تم شحن الرصيد بقيمة {result['amount']:.2f}.", "success")
    except (ValueError, RadiusValidationError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.portal_card_home"))
