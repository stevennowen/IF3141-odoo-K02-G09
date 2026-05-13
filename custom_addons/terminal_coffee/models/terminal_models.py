import base64
import json
import logging
import re
import secrets

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

PHONE_SEPARATOR_RE = re.compile(r"[\s().-]+")
PHONE_ALLOWED_RE = re.compile(r"^\+?[0-9\s().-]+$")


class TerminalDashboard(models.Model):
    _name = "terminal.dashboard"
    _description = "Dashboard Analitik Terminal Coffee"
    _rec_name = "name"

    name = fields.Char(default="Dashboard", required=True)
    customer_count = fields.Integer(compute="_compute_kpis", string="Total Pelanggan")
    segment_count = fields.Integer(compute="_compute_kpis", string="Total Segmen")
    feedback_count = fields.Integer(compute="_compute_kpis", string="Total Feedback")
    average_satisfaction = fields.Float(compute="_compute_kpis", string="Rata-rata Kepuasan", digits=(16, 2))
    campaign_count = fields.Integer(compute="_compute_kpis", string="Total Kampanye")
    interaction_count = fields.Integer(compute="_compute_kpis", string="Total Pengiriman")
    delivered_count = fields.Integer(compute="_compute_kpis", string="Terkirim")
    failed_count = fields.Integer(compute="_compute_kpis", string="Gagal")

    def _compute_kpis(self):
        Customer = self.env["terminal.customer"]
        Segment = self.env["terminal.segment"]
        Feedback = self.env["terminal.survey.feedback"]
        Campaign = self.env["terminal.campaign"]
        Interaction = self.env["terminal.interaction"]
        satisfaction = Feedback.read_group([], ["satisfaction_score:avg"], [])
        average_satisfaction = satisfaction[0]["satisfaction_score"] if satisfaction else 0.0
        for dashboard in self:
            dashboard.customer_count = Customer.search_count([])
            dashboard.segment_count = Segment.search_count([])
            dashboard.feedback_count = Feedback.search_count([])
            dashboard.average_satisfaction = average_satisfaction or 0.0
            dashboard.campaign_count = Campaign.search_count([])
            dashboard.interaction_count = Interaction.search_count([])
            dashboard.delivered_count = Interaction.search_count([("status", "=", "Terkirim")])
            dashboard.failed_count = Interaction.search_count([("status", "ilike", "Gagal")])

    def action_open_customer_segment_analysis(self):
        return self.env.ref("terminal_coffee.action_terminal_customer_segment_analysis").read()[0]

    def action_open_campaign_analysis(self):
        return self.env.ref("terminal_coffee.action_terminal_campaign_analysis").read()[0]

    def action_open_feedback_analysis(self):
        return self.env.ref("terminal_coffee.action_terminal_dashboard_feedback").read()[0]


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

    @api.model
    def _validate_contact_number_format(self, value):
        if not value:
            return
        contact = value.strip()
        compact = PHONE_SEPARATOR_RE.sub("", contact)
        if not PHONE_ALLOWED_RE.match(contact) or compact.count("+") > 1:
            raise ValidationError(_("Nomor WhatsApp hanya boleh berisi angka, spasi, tanda +, -, titik, atau kurung."))
        if compact.startswith("+"):
            compact = compact[1:]
        if not compact.isdigit() or len(compact) < 10 or len(compact) > 15:
            raise ValidationError(_("Nomor WhatsApp harus berisi 10 sampai 15 digit."))
        if not (compact.startswith("08") or compact.startswith("628")):
            raise ValidationError(_("Nomor WhatsApp harus menggunakan format Indonesia, contoh 081234567890 atau +6281234567890."))

    @api.model
    def _normalize_whatsapp_number(self, value):
        self._validate_contact_number_format(value)
        compact = PHONE_SEPARATOR_RE.sub("", value.strip())
        if compact.startswith("+"):
            compact = compact[1:]
        if compact.startswith("628"):
            return "0%s" % compact[2:]
        return compact


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

    segment_id = fields.Many2one("terminal.segment", string="Segmen", required=True, ondelete="restrict")
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
            customer._validate_contact_number_format(customer.contact_number_display)
            customer.contact_number = customer._encrypt_value(customer.contact_number_display)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            plain = vals.pop("contact_number_display", False) or vals.get("contact_number")
            if plain:
                self._validate_contact_number_format(plain)
                vals["contact_number"] = self._encrypt_value(plain)
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        if "contact_number_display" in vals:
            plain = vals.pop("contact_number_display")
            self._validate_contact_number_format(plain)
            vals["contact_number"] = self._encrypt_value(plain)
        elif vals.get("contact_number"):
            self._validate_contact_number_format(vals["contact_number"])
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
    segment_criteria_id = fields.Many2one("terminal.segment", string="Kriteria Segmen")
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
        if self.segment_criteria_id:
            domain.append(("segment_id", "=", self.segment_criteria_id.id))
        elif self.segment_criteria:
            domain.append(("segment_id.name", "ilike", self.segment_criteria))
        return self.env["terminal.customer"].search(domain)

    def _get_or_create_interaction(self, customer):
        self.ensure_one()
        Interaction = self.env["terminal.interaction"]
        interaction = Interaction.search(
            [("campaign_id", "=", self.id), ("customer_id", "=", customer.id)],
            limit=1,
        )
        if interaction:
            return interaction
        return Interaction.create(
            {
                "campaign_id": self.id,
                "customer_id": customer.id,
                "status": "Siap Dikirim",
            }
        )

    def _send_fonnte_messages(self, payload):
        config = self.env["ir.config_parameter"].sudo()
        token = config.get_param("terminal_coffee.fonnte_token")
        api_url = config.get_param("terminal_coffee.fonnte_api_url") or "https://api.fonnte.com/send"
        if not token:
            raise UserError(
                _("Token Fonnte belum dikonfigurasi. Isi System Parameter terminal_coffee.fonnte_token terlebih dahulu.")
            )

        try:
            response = requests.post(
                api_url,
                headers={"Authorization": token},
                data={"data": json.dumps(payload)},
                timeout=30,
            )
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError:
                result = {"status": True, "raw": response.text}
        except requests.RequestException as error:
            raise UserError(_("Gagal menghubungi Fonnte: %s") % error) from error

        if result.get("status") is False:
            message = result.get("reason") or result.get("detail") or result.get("message") or result
            raise UserError(_("Fonnte menolak pengiriman: %s") % message)
        return result

    def action_distribute_promotions(self):
        self.ensure_one()
        if not self.message:
            raise UserError(_("Isi pesan atau e-voucher wajib diisi sebelum distribusi."))
        customers = self._get_target_customers()
        if not customers:
            raise UserError(_("Tidak ada pelanggan yang sesuai dengan kriteria kampanye."))

        wizard = self.env["terminal.campaign.distribution.wizard"].create(
            {
                "campaign_id": self.id,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "customer_id": customer.id,
                            "selected": True,
                        },
                    )
                    for customer in customers
                ],
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Konfirmasi Target Promosi"),
            "res_model": "terminal.campaign.distribution.wizard",
            "view_mode": "form",
            "res_id": wizard.id,
            "target": "new",
        }

    def _distribute_promotions_to_customers(self, customers):
        self.ensure_one()
        if not self.message:
            raise UserError(_("Isi pesan atau e-voucher wajib diisi sebelum distribusi."))
        if not customers:
            raise UserError(_("Pilih minimal satu pelanggan untuk dikirim promosi."))

        payload = []
        interaction_by_target = {}
        failed_without_number = 0
        for customer in customers:
            interaction = self._get_or_create_interaction(customer)
            if interaction.status == "Terkirim":
                continue
            contact_number = customer.contact_number_display
            if not contact_number:
                interaction.write({"send_date": fields.Datetime.now(), "status": "Gagal - Nomor kosong"})
                failed_without_number += 1
                continue
            try:
                target = customer._normalize_whatsapp_number(contact_number)
            except ValidationError as error:
                interaction.write({"send_date": fields.Datetime.now(), "status": "Gagal - %s" % error})
                failed_without_number += 1
                continue
            payload.append({"target": target, "message": self.message, "delay": "1"})
            interaction_by_target[target] = interaction

        if not payload:
            raise UserError(_("Tidak ada nomor pelanggan yang bisa dikirim atau semua target sudah pernah terkirim."))

        self._send_fonnte_messages(payload)
        for interaction in interaction_by_target.values():
            interaction.write({"send_date": fields.Datetime.now(), "status": "Terkirim"})

        sent_count = len(interaction_by_target)
        message = _("Promosi berhasil dikirim ke %s pelanggan melalui Fonnte.") % sent_count
        if failed_without_number:
            message = _("%s %s pelanggan gagal karena nomor kosong atau tidak valid.") % (
                message,
                failed_without_number,
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Kampanye diproses"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }


class TerminalCampaignDistributionWizard(models.TransientModel):
    _name = "terminal.campaign.distribution.wizard"
    _description = "Konfirmasi Distribusi Kampanye"

    campaign_id = fields.Many2one("terminal.campaign", string="Kampanye", required=True, readonly=True)
    line_ids = fields.One2many(
        "terminal.campaign.distribution.wizard.line",
        "wizard_id",
        string="Target Pelanggan",
    )

    def action_check_all(self):
        for wizard in self:
            wizard.line_ids.write({"selected": True})
        return self._reload_wizard()

    def action_uncheck_all(self):
        for wizard in self:
            wizard.line_ids.write({"selected": False})
        return self._reload_wizard()

    def action_send_selected(self):
        self.ensure_one()
        customers = self.line_ids.filtered("selected").mapped("customer_id")
        return self.campaign_id._distribute_promotions_to_customers(customers)

    def _reload_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Konfirmasi Target Promosi"),
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }


class TerminalCampaignDistributionWizardLine(models.TransientModel):
    _name = "terminal.campaign.distribution.wizard.line"
    _description = "Baris Target Distribusi Kampanye"
    _order = "customer_id"

    wizard_id = fields.Many2one(
        "terminal.campaign.distribution.wizard",
        string="Wizard",
        required=True,
        ondelete="cascade",
    )
    selected = fields.Boolean(string="Kirim", default=True)
    customer_id = fields.Many2one("terminal.customer", string="Pelanggan", required=True, readonly=True)
    segment_id = fields.Many2one(related="customer_id.segment_id", string="Segmen", readonly=True)
    loyalty_score = fields.Integer(related="customer_id.loyalty_score", string="Skor Loyalitas", readonly=True)
    contact_number_display = fields.Char(related="customer_id.contact_number_display", string="No WhatsApp", readonly=True)


class TerminalInteraction(models.Model):
    _name = "terminal.interaction"
    _description = "Riwayat Interaksi"
    _order = "send_date desc"

    campaign_id = fields.Many2one("terminal.campaign", string="Kampanye", required=True, ondelete="cascade")
    customer_id = fields.Many2one("terminal.customer", string="Pelanggan", required=True, ondelete="restrict")
    segment_id = fields.Many2one(related="customer_id.segment_id", string="Segmen", store=True, readonly=True)
    send_date = fields.Datetime(string="Tgl Kirim", default=fields.Datetime.now, required=True)
    status = fields.Char(string="Status", default="Terkirim", required=True)
