from odoo import fields, http
from odoo.exceptions import ValidationError
from odoo.http import request


class TerminalSurveyController(http.Controller):
    @http.route("/survey/fill/<int:form_id>", type="http", auth="public", website=False, methods=["GET"])
    def survey_fill(self, form_id, **kwargs):
        survey_form = request.env["terminal.survey.form"].sudo().browse(form_id)
        if not survey_form.exists() or not survey_form.is_active:
            return request.render(
                "terminal_coffee.survey_unavailable",
                {"message": "Form survei tidak tersedia atau belum aktif."},
            )
        return request.render(
            "terminal_coffee.survey_fill",
            {
                "survey_form": survey_form,
                "segments": request.env["terminal.segment"].sudo().search([]),
                "errors": {},
                "values": {},
            },
        )

    @http.route("/survey/fill/<int:form_id>", type="http", auth="public", website=False, methods=["POST"], csrf=True)
    def survey_submit(self, form_id, **post):
        survey_form = request.env["terminal.survey.form"].sudo().browse(form_id)
        if not survey_form.exists() or not survey_form.is_active:
            return request.render(
                "terminal_coffee.survey_unavailable",
                {"message": "Form survei tidak tersedia atau belum aktif."},
            )

        errors = self._validate_submission(survey_form, post)
        if errors:
            return request.render(
                "terminal_coffee.survey_fill",
                {
                    "survey_form": survey_form,
                    "segments": request.env["terminal.segment"].sudo().search([]),
                    "errors": errors,
                    "values": post,
                },
            )

        Customer = request.env["terminal.customer"].sudo()
        Feedback = request.env["terminal.survey.feedback"].sudo()
        Answer = request.env["terminal.survey.answer"].sudo()

        customer_name = post.get("customer_name", "").strip()
        contact_number = post.get("contact_number", "").strip()
        segment_id = int(post.get("segment_id"))
        satisfaction_score = int(post.get("satisfaction_score"))

        domain = [("name", "=ilike", customer_name)]
        existing_customer = Customer.search(domain, limit=1)
        if existing_customer:
            values = {
                "segment_id": segment_id,
                "loyalty_score": existing_customer.loyalty_score + 1,
            }
            if contact_number:
                values["contact_number_display"] = contact_number
            existing_customer.write(values)
            customer = existing_customer
        else:
            customer = Customer.create(
                {
                    "name": customer_name,
                    "segment_id": segment_id,
                    "contact_number_display": contact_number,
                    "loyalty_score": 1,
                }
            )

        feedback = Feedback.create(
            {
                "customer_id": customer.id,
                "form_id": survey_form.id,
                "satisfaction_score": satisfaction_score,
                "comment": post.get("comment", "").strip(),
                "fill_date": fields.Datetime.now(),
            }
        )

        for question in survey_form.question_ids:
            answer_value = post.get("question_%s" % question.id, "").strip()
            if answer_value:
                Answer.create(
                    {
                        "feedback_id": feedback.id,
                        "question_id": question.id,
                        "answer_text": answer_value,
                    }
                )

        return request.render("terminal_coffee.survey_success", {"survey_form": survey_form})

    def _validate_submission(self, survey_form, post):
        errors = {}
        if not post.get("customer_name", "").strip():
            errors["customer_name"] = "Nama wajib diisi."

        segment_id = post.get("segment_id")
        if not segment_id:
            errors["segment_id"] = "Segmen wajib dipilih."
        else:
            try:
                segment = request.env["terminal.segment"].sudo().browse(int(segment_id))
                if not segment.exists():
                    errors["segment_id"] = "Segmen tidak valid."
            except ValueError:
                errors["segment_id"] = "Segmen tidak valid."

        contact_number = post.get("contact_number", "").strip()
        if contact_number:
            try:
                request.env["terminal.customer"].sudo()._validate_contact_number_format(contact_number)
            except ValidationError as error:
                errors["contact_number"] = str(error)

        try:
            score = int(post.get("satisfaction_score", "0"))
            if score < 1 or score > 5:
                errors["satisfaction_score"] = "Skor fasilitas harus berada pada rentang 1 sampai 5."
        except ValueError:
            errors["satisfaction_score"] = "Skor fasilitas wajib dipilih."

        for question in survey_form.question_ids:
            answer_key = "question_%s" % question.id
            if question.is_required and not post.get(answer_key, "").strip():
                errors[answer_key] = "Pertanyaan ini wajib diisi."
            if question.answer_type == "scale" and post.get(answer_key):
                try:
                    value = int(post.get(answer_key))
                    if value < 1 or value > 5:
                        errors[answer_key] = "Jawaban skala harus berada pada rentang 1 sampai 5."
                except ValueError:
                    errors[answer_key] = "Jawaban skala tidak valid."
        return errors
