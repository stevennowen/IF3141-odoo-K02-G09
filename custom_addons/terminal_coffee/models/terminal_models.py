import base64
import logging
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class TerminalEncryptionMixin(models.AbstractModel):
    _name = "terminal.encryption.mixin"
    _description = "Terminal Coffee AES-256 Encryption Helper"

    def _get_aesgcm(self):
        config = self.env["ir.config_parameter"].sudo()
        key = config.get_param("terminal_coffee.encryption_key")
        if not key:
            key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
            config.set_param("terminal_coffee.encryption_key", key)
        return AESGCM(base64.urlsafe_b64decode(key.encode()))

    def _encrypt_value(self, value):
        if not value:
            return False
        if isinstance(value, str) and value.startswith("aes256:"):
            return value
        nonce = secrets.token_bytes(12)
        encrypted = self._get_aesgcm().encrypt(nonce, value.encode(), None)
        token = base64.urlsafe_b64encode(nonce + encrypted).decode()
        return "aes256:%s" % token

    def _decrypt_value(self, value):
        if not value:
            return False
        if not isinstance(value, str) or not value.startswith("aes256:"):
            return value
        try:
            raw = base64.urlsafe_b64decode(value.split("aes256:", 1)[1].encode())
            nonce = raw[:12]
            encrypted = raw[12:]
            return self._get_aesgcm().decrypt(nonce, encrypted, None).decode()
        except Exception:
            _logger.exception("Failed to decrypt Terminal Coffee contact value")
            return _("Unable to decrypt")


class TerminalSegment(models.Model):
    _name = "terminal.segment"
    _description = "Segmen Pelanggan"
    _order = "name"

    name = fields.Char(string="Nama Segmen", required=True)
    description = fields.Text(string="Deskripsi")
    customer_ids = fields.One2many("terminal.customer", "segment_id", string="Pelanggan")
    customer_count = fields.Integer(compute="_compute_customer_count", string="Jumlah Pelanggan")

    def _compute_customer_count(self):
        grouped = self.env["terminal.customer"].read_group(
            [("segment_id", "in", self.ids)], ["segment_id"], ["segment_id"]
        )
        counts = {item["segment_id"][0]: item["segment_id_count"] for item in grouped}
        for segment in self:
            segment.customer_count = counts.get(segment.id, 0)


class TerminalCustomer(models.Model):
    _name = "terminal.customer"
    _description = "Pelanggan Terminal Coffee"
    _inherit = ["terminal.encryption.mixin"]
    _order = "loyalty_score desc, name"

    segment_id = fields.Many2one("terminal.segment", string="Segmen")
    name = fields.Char(string="Nama", required=True)
    contact_number = fields.Char(string="No Kontak Terenkripsi", copy=False)
    contact_number_display = fields.Char(
        string="No WhatsApp", compute="_compute_contact_number_display", inverse="_inverse_contact_number_display"
    )
    loyalty_score = fields.Integer(string="Skor Loyalitas", default=0)
    feedback_ids = fields.One2many("terminal.survey.feedback", "customer_id", string="Feedback")
    interaction_ids = fields.One2many("terminal.interaction", "customer_id", string="Interaksi")

    @api.depends("contact_number")
    def _compute_contact_number_display(self):
        for customer in self:
            customer.contact_number_display = customer._decrypt_value(customer.contact_number)

    def _inverse_contact_number_display(self):
        for customer in self:
            customer.contact_number = customer._encrypt_value(customer.contact_number_display)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            plain = vals.pop("contact_number_display", False) or vals.get("contact_number")
            if plain:
                vals["contact_number"] = self._encrypt_value(plain)
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        if "contact_number_display" in vals:
            vals["contact_number"] = self._encrypt_value(vals.pop("contact_number_display"))
        elif vals.get("contact_number"):
            vals["contact_number"] = self._encrypt_value(vals["contact_number"])
        return super().write(vals)


class TerminalSurveyForm(models.Model):
    _name = "terminal.survey.form"
    _description = "Form Kuesioner"
    _order = "create_date desc"

    name = fields.Char(string="Nama Form", required=True, default="Survey Kepuasan Terminal Coffee")
    is_active = fields.Boolean(string="Status Aktif", default=True)
    create_date = fields.Datetime(string="Tgl Dibuat", readonly=True)
    question_ids = fields.One2many(
        "terminal.survey.question", "form_id", string="Pertanyaan", copy=True
    )
    feedback_ids = fields.One2many("terminal.survey.feedback", "form_id", string="Feedback")
    feedback_count = fields.Integer(compute="_compute_feedback_count", string="Jumlah Feedback")
    average_satisfaction = fields.Float(
        compute="_compute_average_satisfaction", string="Rata-rata Kepuasan", digits=(16, 2)
    )
    public_url = fields.Char(compute="_compute_public_url", string="URL Survey Publik")

    def _compute_feedback_count(self):
        grouped = self.env["terminal.survey.feedback"].read_group(
            [("form_id", "in", self.ids)], ["form_id"], ["form_id"]
        )
        counts = {item["form_id"][0]: item["form_id_count"] for item in grouped}
        for form in self:
            form.feedback_count = counts.get(form.id, 0)

    def _compute_average_satisfaction(self):
        grouped = self.env["terminal.survey.feedback"].read_group(
            [("form_id", "in", self.ids)], ["satisfaction_score:avg"], ["form_id"]
        )
        averages = {item["form_id"][0]: item["satisfaction_score"] for item in grouped}
        for form in self:
            form.average_satisfaction = averages.get(form.id, 0.0)

    def _compute_public_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for form in self:
            form.public_url = "%s/survey/fill/%s" % (base_url, form.id) if form.id else False


class TerminalSurveyQuestion(models.Model):
    _name = "terminal.survey.question"
    _description = "Pertanyaan Survei"
    _order = "sequence, id"

    form_id = fields.Many2one(
        "terminal.survey.form", string="Form Kuesioner", required=True, ondelete="cascade"
    )
    question_text = fields.Text(string="Pertanyaan", required=True)
    answer_type = fields.Selection(
        [("scale", "Skala 1-5"), ("text", "Teks")], string="Tipe Jawaban", required=True, default="scale"
    )
    sequence = fields.Integer(string="Urutan", default=10)
    is_required = fields.Boolean(string="Wajib Diisi", default=True)


class TerminalSurveyFeedback(models.Model):
    _name = "terminal.survey.feedback"
    _description = "Feedback Survei"
    _order = "fill_date desc"

    customer_id = fields.Many2one("terminal.customer", string="Pelanggan", required=True, ondelete="restrict")
    form_id = fields.Many2one("terminal.survey.form", string="Form Kuesioner", required=True, ondelete="restrict")
    satisfaction_score = fields.Integer(string="Skor Kepuasan", required=True)
    comment = fields.Text(string="Komentar / Keluhan")
    fill_date = fields.Datetime(string="Tgl Isi", default=fields.Datetime.now, required=True)
    answer_ids = fields.One2many("terminal.survey.answer", "feedback_id", string="Jawaban")

    @api.constrains("satisfaction_score")
    def _check_satisfaction_score(self):
        for feedback in self:
            if feedback.satisfaction_score < 1 or feedback.satisfaction_score > 5:
                raise ValidationError(_("Skor kepuasan harus berada pada rentang 1 sampai 5."))


class TerminalSurveyAnswer(models.Model):
    _name = "terminal.survey.answer"
    _description = "Jawaban Survei"
    _order = "question_id"

    feedback_id = fields.Many2one(
        "terminal.survey.feedback", string="Feedback", required=True, ondelete="cascade"
    )
    question_id = fields.Many2one(
        "terminal.survey.question", string="Pertanyaan", required=True, ondelete="restrict"
    )
    answer_text = fields.Text(string="Isi Jawaban")


class TerminalCampaign(models.Model):
    _name = "terminal.campaign"
    _description = "Kampanye Promosi"
    _order = "create_date desc"

    name = fields.Char(string="Nama Kampanye", required=True)
    promotion_type = fields.Char(string="Jenis Promosi", required=True)
    segment_criteria = fields.Char(string="Kriteria Segmen")
    loyalty_min_score = fields.Integer(string="Minimum Skor Loyalitas", default=5)
    message = fields.Text(string="Pesan / E-Voucher")
    create_date = fields.Datetime(string="Tgl Dibuat", readonly=True)
    interaction_ids = fields.One2many("terminal.interaction", "campaign_id", string="Riwayat Pengiriman")
    interaction_count = fields.Integer(compute="_compute_interaction_count", string="Jumlah Pengiriman")

    def _compute_interaction_count(self):
        grouped = self.env["terminal.interaction"].read_group(
            [("campaign_id", "in", self.ids)], ["campaign_id"], ["campaign_id"]
        )
        counts = {item["campaign_id"][0]: item["campaign_id_count"] for item in grouped}
        for campaign in self:
            campaign.interaction_count = counts.get(campaign.id, 0)

    def _get_target_customers(self):
        self.ensure_one()
        domain = [("loyalty_score", ">=", self.loyalty_min_score)]
        if self.segment_criteria:
            domain.append(("segment_id.name", "ilike", self.segment_criteria))
        return self.env["terminal.customer"].search(domain)

    def action_distribute_promotions(self):
        Interaction = self.env["terminal.interaction"]
        for campaign in self:
            customers = campaign._get_target_customers()
            if not customers:
                raise UserError(_("Tidak ada pelanggan yang sesuai dengan kriteria kampanye."))
            existing_pairs = set(
                Interaction.search([("campaign_id", "=", campaign.id), ("customer_id", "in", customers.ids)]).mapped(
                    lambda item: item.customer_id.id
                )
            )
            for customer in customers:
                if customer.id not in existing_pairs:
                    Interaction.create(
                        {
                            "campaign_id": campaign.id,
                            "customer_id": customer.id,
                            "send_date": fields.Datetime.now(),
                            "status": "Terkirim",
                        }
                    )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Kampanye diproses"),
                "message": _("Riwayat pengiriman promosi berhasil dibuat."),
                "type": "success",
                "sticky": False,
            },
        }


class TerminalInteraction(models.Model):
    _name = "terminal.interaction"
    _description = "Riwayat Interaksi"
    _order = "send_date desc"

    campaign_id = fields.Many2one("terminal.campaign", string="Kampanye", required=True, ondelete="cascade")
    customer_id = fields.Many2one("terminal.customer", string="Pelanggan", required=True, ondelete="restrict")
    send_date = fields.Datetime(string="Tgl Kirim", default=fields.Datetime.now, required=True)
    status = fields.Char(string="Status", default="Terkirim", required=True)
