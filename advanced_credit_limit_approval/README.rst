===========================================
Advanced Credit Limit & Sequential Approval
===========================================

Odoo 19 (Community & Enterprise). Arabic (``ar_001``) translation included.

Configuration
=============

1. *Sales > Configuration > Settings > Credit Limit Control*: enable **Credit
   Limit Tracking** and choose the **Blacklisted Customer Policy**
   (*Hard Block* or *Force Approval*). Optionally enable **Auto-lift Blacklist**.
2. *Sales > Configuration > Sales Teams > Approvers*: list the approvers in
   their strict order (drag the handle to reorder).
3. Customer form > *Credit Control* tab: set the **Warning** and **Blocking**
   amounts (Sales Administrators / Accounting Advisers only). Limits live on
   the commercial entity and are shared by all its contacts.

Exposure
========

Per company, consolidated on ``commercial_partner_id``, in company currency::

    exposure = open posted receivables
             + un-invoiced part of confirmed orders (tax included,
               net of posted invoices and credit notes)
             - draft customer payments (only with "Deduct Draft Payments")

Behaviour
=========

* **Warning breach** – e-mail to the customer, chatter note, *Credit Limit
  Follow-Up* activity for the customer's salesperson (fallback: order
  salesperson).
* **Blocking breach** – the above + *Blacklisted* tag and a black ribbon on
  the customer form.
* The displayed *Credit Status* is computed live from the current exposure.
* Breach actions run on order confirmation, changes to confirmed orders,
  order cancellation, invoice/payment posting, reset to draft, limit changes,
  a daily scheduled action and the *Re-evaluate* button. They only fire on
  escalation (no duplicates).
* A confirmed order cannot be increased when the new exposure exceeds the
  Blocking amount or the customer is blacklisted: the extra amount must go on
  a new quotation, which goes through approval. Down payments and re-invoiced
  expenses are not blocked.
* Limits cannot be negative, and the Warning amount cannot exceed the
  Blocking amount.
* Draft customer payments reduce the exposure only when *Deduct Draft
  Payments* is enabled (off by default).
* An order whose projected exposure exceeds the Blocking amount, or whose
  customer is blacklisted (Force Approval policy), goes to **Waiting
  Approval**. Each tier can only be approved/rejected by its own user; the
  last approval confirms the order. Rejection cancels the order, logs the
  reason and e-mails the customer. The salesperson or a Sales Administrator
  can withdraw the order back to Quotation.
* Orders waiting for approval are locked (header and lines).

Tests
=====

::

    odoo-bin -d <test_db> -i advanced_credit_limit_approval \
        --test-tags /advanced_credit_limit_approval --stop-after-init

The suite (``post_install``) needs a chart of accounts (``l10n_generic_coa``
or any localization).
