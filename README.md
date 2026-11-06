# Advanced Credit Limit & Sequential Approval

Odoo **16.0** module (Community & Enterprise) &middot; technical name `advanced_credit_limit_approval` &middot; LGPL-3

Real-time customer credit exposure, Warning / Blocking limits with automatic e-mail,
chatter log and salesperson follow-up, automatic blacklisting with a black ribbon, and
strict sequential Sales Team approvals for risky orders. Fully translated to Arabic
(`ar_001`) with RTL support.

## Branches

Each Odoo version has its own branch; pick the one that matches your server.

| Odoo | Branch | Editions |
|------|--------|----------|
| **16.0** | [`16.0`](https://github.com/ibrahim77818/advanced_credit_limit_approval/tree/16.0) | Community & Enterprise |
| 17.0 | [`17.0`](https://github.com/ibrahim77818/advanced_credit_limit_approval/tree/17.0) | Community & Enterprise |
| 18.0 | [`18.0`](https://github.com/ibrahim77818/advanced_credit_limit_approval/tree/18.0) | Community & Enterprise |
| 19.0 | [`19.0`](https://github.com/ibrahim77818/advanced_credit_limit_approval/tree/19.0) | Community & Enterprise |

## Features

- **Credit Limit Tracking** switch in *Sales › Configuration › Settings › Credit Limit Control* (per company, off by default).
- **Blacklisted Customer Policy**: *Hard Block* (no sales orders) or *Force Approval* (quotations allowed, confirmation always approved).
- **Real-time exposure** per commercial entity, in company currency:
  open posted receivables + un-invoiced part of confirmed orders (tax included, net of posted invoices and credit notes) − draft payments *(only with **Deduct Draft Payments**, off by default)*.
- **Warning / Blocking amounts** on the customer (*Credit Control* tab), validated: no negative amounts, warning ≤ blocking. Editable by Sales Administrators and Accounting Advisers only.
- **Live Credit Status** computed from the current figures.
- **Breach actions** (only on escalation, no duplicates): e-mail to the customer, chatter note, *Credit Limit Follow-Up* activity for the salesperson; on blocking, the **Blacklisted** tag and a black `web_ribbon` on the customer form.
- **Immediate re-evaluation** on order confirmation, changes to confirmed orders, cancellation, invoice/payment posting, reset to draft, limit changes, the daily scheduled action and the *Re-evaluate Credit Status* button.
- **Confirmed orders cannot be enlarged past the limit**: the extra amount must go on a new quotation. Down payments and re-invoiced expenses are not blocked.
- **Blacklisted-customer alert** when the customer is selected on a quotation.
- **Sequential approvals** defined per Sales Team (*Approvers* tab). Over-limit orders go to **Waiting Approval**; only the current tier can *Approve* or *Reject*; the last approval confirms the order. *Reject* asks for a reason, cancels the order and e-mails the customer. The salesperson or a Sales Administrator can *Withdraw from Approval*.
- **Orders to Approve** menu (*Sales › Orders*) filtered on *My Credit Approvals*.
- Waiting orders are locked (header and lines) and cannot be confirmed or cancelled directly.

## Installation

1. Copy the `advanced_credit_limit_approval` folder of the `16.0` branch into your addons path
   (or add this repository to your Odoo.sh project on branch `16.0`).
2. Update the Apps list and install **Advanced Credit Limit & Sequential Approval**.
3. Dependencies: `sale_management`, `account`. No extra Python packages.

```bash
git clone -b 16.0 https://github.com/ibrahim77818/advanced_credit_limit_approval.git
```

## Configuration

1. *Sales › Configuration › Settings › Credit Limit Control*: enable **Credit Limit Tracking**, choose the policy, optionally enable **Deduct Draft Payments** and **Auto-lift Blacklist**.
2. *Sales › Configuration › Sales Teams › Approvers*: add the approvers in their strict order.
3. Customer form › *Credit Control*: set the **Warning** and **Blocking** amounts on the company (contacts inherit them).

## Tests

38 `TransactionCase` tests (tagged `acla`, `post_install`) cover exposure calculation, live status,
breach actions and activities, blacklist policies, increases on confirmed orders, limit validation,
sequential approval security, rejection, withdrawal and bypass protection.

```bash
odoo-bin -d <test_db> -i advanced_credit_limit_approval \
    --test-tags /advanced_credit_limit_approval --stop-after-init
```

A chart of accounts must be installable on the test database.

## Support

Author: **Ibrahim Yahia** &middot; [LinkedIn](https://www.linkedin.com/in/ibrahim-yahia) &middot; ibrahim.yahiah@gmail.com

License: [LGPL-3](https://www.gnu.org/licenses/lgpl-3.0.html)
