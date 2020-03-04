import frappe
import json
from frappe.utils import cint
from frappe.translate import load_lang, get_user_translations, get_messages_for_app

@frappe.whitelist(allow_guest=True)
def add_translation(data):
	data = json.loads(data)
	if frappe.db.get_all("Contributed Translation", filters={"source_string": data.get("source_name"), "translated_string": data.get("target_name"), "language": data.get("language")}):
		return {
			"message": "Already exists"
		}
	else:
		contributed_translation = frappe.get_doc({
			"doctype": "Contributed Translation",
			"language": data.get("language"),
			"contributor": data.get("contributor"),
			"source_string": data.get("source_name"),
			"translated_string": data.get("target_name"),
			"posting_date": data.get("posting_date")
		}).insert(ignore_permissions = True)
		return {
			"message": "Added to contribution list",
			"doc_name": contributed_translation.name
		}

@frappe.whitelist(allow_guest=True)
def translation_status(data):
	data = json.loads(data)
	status = frappe.db.get_value("Contributed Translation", data.get("doc_name"), "status")
	if status:
		return {
			"status": status
		}
	else:
		return {
			"message": "Contributed Translation has been deleted"
		}

@frappe.whitelist(allow_guest=True)
def add_translations(translation_map, contributor_name, contributor_email, language):
	translation_map = json.loads(translation_map)
	for source_text, translation_dict in translation_map.items():
		translation_dict = frappe._dict(translation_dict)
		existing_doc_name = frappe.db.exists('Contributed Translation', {
			'source_name': source_text,
			'context': translation_dict.context,
			'contributor_email': contributor_email
		})
		if existing_doc_name:
			frappe.set_value('Contributed Translation', existing_doc_name, 'target_name', translation_dict.translated_text)
		else:
			doc = frappe.get_doc({
				'doctype': 'Contributed Translation',
				'source_string': source_text,
				'translated_string': translation_dict.translated_text,
				'context': translation_dict.context,
				'contributor_email': contributor_email,
				'contributor_name': contributor_name,
				'language': language
			})
			doc.insert(ignore_permissions = True)


@frappe.whitelist(allow_guest=True)
def get_strings_for_translation(language, start=0, page_length=1000, search_text=''):

	start = cint(start)
	page_length = cint(page_length)

	apps = frappe.get_all_apps(True)

	messages = []
	translated_message_dict = load_lang(lang=language)
	contributed_translations = get_contributed_translations(language)

	app_messages = frappe.cache().hget('app_messages', language) or []
	if not app_messages:
		for app in apps:
			app_messages += get_messages_for_app(app)

		frappe.cache().hset('app_messages', language, app_messages)

	for message in app_messages:
		path_or_doctype = message[0] or ''
		source_text = message[1]
		line = None
		context = None

		if len(message) > 2:
			context = message[2]
			line = message[3]

		doctype = path_or_doctype.rsplit('DocType: ')[1] if path_or_doctype.startswith('DocType:') else None

		source_key = source_text
		if context:
			source_key += ':' + context

		translated_text = translated_message_dict.get(source_key)

		messages.append(frappe._dict({
			'id': source_text,
			'source_text': source_text,
			'translated_text': translated_text or '',
			'user_translated': bool(False),
			'context': context,
			'line': line,
			'path': path_or_doctype if not doctype else None,
			'doctype': doctype,
			'contributions': contributed_translations.get(source_text) or []
		}))

	frappe.clear_messages()

	if search_text:
		messages = [message for message in messages if search_text in message.source_text]

	messages = sorted(messages, key=lambda x: x.translated_text, reverse=False)

	return messages[start:start + page_length]

def get_contributed_translations(language):
	cached_records = frappe.cache().hget('contributed_translations', language)
	if cached_records:
		return cached_records

	doc_list = frappe.get_all('Contributed Translation',
		fields=['source_string', 'translated_string', 'status', 'creation',
			'language', 'contributor_email', 'contributor_name', 'modified_by'],
		filters={
			'language': language,
			'status': ['!=', 'Rejected'],
		}, order_by='verified')

	doc_map = {}
	for doc in doc_list:
		if doc_map.get(doc.source_string):
			doc_map[doc.source_string].append(doc)
		else:
			doc_map[doc.source_string] = [doc]

	frappe.cache().hset('contributed_translations', language, doc_map)

	return doc_map