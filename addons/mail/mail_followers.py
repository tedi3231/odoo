# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2009-today OpenERP SA (<http://www.openerp.com>)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>
#
##############################################################################

from openerp import SUPERUSER_ID
from osv import osv
from osv import fields
import tools


class mail_followers(osv.Model):
    """ mail_followers holds the data related to the follow mechanism inside
        OpenERP. Partners can choose to follow documents (records) of any kind
        that inherits from mail.thread. Following documents allow to receive
        notifications for new messages.
        A subscription is characterized by:
            :param: res_model: model of the followed objects
            :param: res_id: ID of resource (may be 0 for every objects)
    """
    _name = 'mail.followers'
    _rec_name = 'partner_id'
    _log_access = False
    _description = 'Document Followers'
    _columns = {
        'res_model': fields.char('Related Document Model', size=128,
                        required=True, select=1,
                        help='Model of the followed resource'),
        'res_id': fields.integer('Related Document ID', select=1,
                        help='Id of the followed resource'),
        'partner_id': fields.many2one('res.partner', string='Related Partner',
                        ondelete='cascade', required=True, select=1),
        'subtype_ids': fields.many2many('mail.message.subtype', string='Subtype',
            help="Message subtypes followed, meaning subtypes that will be pushed onto the user's Wall."),
    }


class mail_notification(osv.Model):
    """ Class holding notifications pushed to partners. Followers and partners
        added in 'contacts to notify' receive notifications. """
    _name = 'mail.notification'
    _rec_name = 'partner_id'
    _log_access = False
    _description = 'Notifications'

    _columns = {
        'partner_id': fields.many2one('res.partner', string='Contact',
                        ondelete='cascade', required=True, select=1),
        'read': fields.boolean('Read', select=1),
        'message_id': fields.many2one('mail.message', string='Message',
                        ondelete='cascade', required=True, select=1),
    }

    _defaults = {
        'read': False,
    }

    def init(self, cr):
        cr.execute('SELECT indexname FROM pg_indexes WHERE indexname = %s', ('mail_notification_partner_id_read_message_id',))
        if not cr.fetchone():
            cr.execute('CREATE INDEX mail_notification_partner_id_read_message_id ON mail_notification (partner_id, read, message_id)')

    def create(self, cr, uid, vals, context=None):
        """ Override of create to check that we can not create a notification
            for a message the user can not read. """
        if self.pool.get('mail.message').check_access_rights(cr, uid, 'read'):
            return super(mail_notification, self).create(cr, uid, vals, context=context)
        return False

    def set_message_read(self, cr, uid, msg_ids, read=None, context=None):
        """ Set messages as (un)read. Technically, the notifications related
            to uid are set to (un)read. If for some msg_ids there are missing
            notifications (i.e. due to load more or thread parent fetching),
            they are created.

            :param bool read: (un)read notification
        """
        user_pid = self.pool.get('res.users').read(cr, uid, uid, ['partner_id'], context=context)['partner_id'][0]
        notif_ids = self.search(cr, uid, [
            ('partner_id', '=', user_pid),
            ('message_id', 'in', msg_ids)
            ], context=context)

        # all message have notifications: already set them as (un)read
        if len(notif_ids) == len(msg_ids):
            return self.write(cr, uid, notif_ids, {'read': read}, context=context)

        # some messages do not have notifications: find which one, create notification, update read status
        notified_msg_ids = [notification.message_id.id for notification in self.browse(cr, uid, notif_ids, context=context)]
        to_create_msg_ids = list(set(msg_ids) - set(notified_msg_ids))
        for msg_id in to_create_msg_ids:
            self.create(cr, uid, {'partner_id': user_pid, 'read': read, 'message_id': msg_id}, context=context)
        return self.write(cr, uid, notif_ids, {'read': read}, context=context)

    def get_partners_to_notify(self, cr, uid, message, context=None):
        """ Return the list of partners to notify, based on their preferences.

            :param browse_record message: mail.message to notify
        """
        notify_pids = []
        for notification in message.notification_ids:
            if notification.read:
                continue
            partner = notification.partner_id
            # Do not send an email to the writer
            if partner.user_ids and partner.user_ids[0].id == uid:
                continue
            # Do not send to partners without email address defined
            if not partner.email:
                continue
            # Partner does not want to receive any emails
            if partner.notification_email_send == 'none':
                continue
            # Partner wants to receive only emails and comments
            if partner.notification_email_send == 'comment' and message.type not in ('email', 'comment'):
                continue
            # Partner wants to receive only emails
            if partner.notification_email_send == 'email' and message.type != 'email':
                continue
            notify_pids.append(partner.id)
        return notify_pids

    def _notify(self, cr, uid, msg_id, context=None):
        """ Send by email the notification depending on the user preferences """
        if context is None:
            context = {}
        # mail_noemail (do not send email) or no partner_ids: do not send, return
        if context.get('mail_noemail'):
            return True
        msg = self.pool.get('mail.message').browse(cr, uid, msg_id, context=context)

        notify_partner_ids = self.get_partners_to_notify(cr, uid, msg, context=context)
        if not notify_partner_ids:
            return True

        # add the context in the email
        # TDE FIXME: commented, to be improved in a future branch
        # quote_context = self.pool.get('mail.message').message_quote_context(cr, uid, msg_id, context=context)

        mail_mail = self.pool.get('mail.mail')
        # add signature
        body_html = msg.body
        # if quote_context:
            # body_html = tools.append_content_to_html(body_html, quote_context, plaintext=False)
        signature = msg.author_id and msg.author_id.user_ids and msg.author_id.user_ids[0].signature or ''
        if signature:
            body_html = tools.append_content_to_html(body_html, signature, plaintext=True, container_tag='div')

        mail_values = {
            'mail_message_id': msg.id,
            'email_to': [],
            'auto_delete': True,
            'body_html': body_html,
            'state': 'outgoing',
        }
        mail_values['email_to'] = ', '.join(mail_values['email_to'])
        email_notif_id = mail_mail.create(cr, uid, mail_values, context=context)
        return mail_mail.send(cr, uid, [email_notif_id], recipient_ids=notify_partner_ids, context=context)