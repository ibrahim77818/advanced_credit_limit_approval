# -*- coding: utf-8 -*-
{
    'name': 'Advanced Credit Limit & Sequential Approval',
    'version': '19.0.1.1.0',
    'category': 'Sales/Sales',
    'summary': 'Real-time customer credit exposure, warning/blocking limits, '
               'automatic blacklisting and sequential Sales Team approvals.',
    'description': """
Advanced Credit Limit & Sequential Approval
===========================================
* Company-level switch to enable credit limit tracking (Sales Settings).
* Real-time exposure per commercial entity: posted receivables
  + un-invoiced confirmed sales (- draft customer payments, optional).
* Per-customer Warning / Blocking amounts with automatic e-mail, chatter log
  and follow-up activity for the salesperson.
* Automatic "Blacklisted" tag with a black ribbon on the customer form.
* Hard Block or Force Approval policy for blacklisted customers.
* Strict sequential approvals per Sales Team; reject cancels the order and
  e-mails the customer.
* Full Arabic (ar_001) translation and RTL-safe styling.
""",
    'author': 'Ibrahim Yahia',
    'website': 'https://www.linkedin.com/in/ibrahim-yahia',
    'license': 'LGPL-3',
    'depends': ['sale_management', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'security/acla_security.xml',
        'data/res_partner_category_data.xml',
        'data/mail_activity_type_data.xml',
        'data/mail_template_data.xml',
        'data/ir_cron_data.xml',
        'wizard/acla_reject_wizard_views.xml',
        'views/res_config_settings_views.xml',
        'views/res_partner_views.xml',
        'views/crm_team_views.xml',
        'views/sale_order_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'advanced_credit_limit_approval/static/src/scss/acla.scss',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
