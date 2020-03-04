# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

from __future__ import unicode_literals, absolute_import
import frappe, os

from frappe.translate import read_csv_file, get_all_languages, write_translations_file, get_messages_for_app
from translator.translator.doctype.translated_message.translated_message import get_placeholders_count
import frappe.utils
from frappe.utils import strip, update_progress_bar
from frappe import safe_decode
import json
from csv import writer
from git import Repo
import csv
import frappe.exceptions
from six import iteritems

def import_source_messages():
	"""Import messages from apps listed in **Translator App** as **Source Message**"""
	frappe.db.sql("UPDATE `tabSource Message` SET `disabled`=1")
	for app in get_apps_to_be_translated():
		app_version = frappe.get_hooks(app_name='frappe')['app_version'][0]
		messages = get_messages_for_app(app)
		# messages structure
		# [(position, source_text_1, context), (position, source_text_2)]

		for message in messages:
			context = ''
			if len(message) > 2 and message[2]:
				context = message[2]

			source_message = frappe.db.get_all('Source Message', {
				'message': message[1],
				'context': context,
				'app': app,
			}, ['name', 'message', 'position', 'app_version', 'context'], limit=1)

			source_message = source_message[0] if source_message else None
			if source_message:
				d = frappe.get_doc("Source Message", source_message['name'])
				if source_message["position"] != message[0]:
					d.position = message[0]
				if source_message['app_version'] != app_version:
					d.app_version = app_version
				d.disabled = 0
			else:
				d = frappe.new_doc('Source Message')
				d.position = message[0]
				d.message = message[1]
				d.app = app
				d.context = context
				d.app_version = app_version
			d.save()

def write_csv_for_all_languages():
	langs = frappe.db.sql_list("select name from tabLanguage")
	for lang in langs:
		for app in get_apps_to_be_translated():
			print("Writing for {0}-{1}".format(app, lang))
			path = os.path.join(frappe.get_app_path(app, "translations", lang + ".csv"))
			write_csv(app, lang, path)

def write_csv(app, lang, path):
	translations = get_translations_for_export(app, lang)

	parent = None
	parent_dict = {}
	if '-' in lang:
		# get translation from parent
		# for example es and es-GT
		parent = lang.split('-')[0]
		parent_dict = {}
		for t in get_translations_for_export(app, parent):
			parent_dict[t.source_text] = t.translated_text

	with open(path, 'w') as msgfile:
		w = writer(msgfile, lineterminator='\n')
		for t in translations:
			# only write if translation is different from parent
			if (not parent) or (t.translated_text != parent_dict.get(t.source_text)):
				position = t.position or ''
				translated_text = strip(t.translated_text or '')
				context = strip(t.context or '')
				w.writerow([position, t.source_text, translated_text, context])


def write_translations_and_commit():
	langs = frappe.db.sql_list("select name from tabLanguage")
	for app in get_apps_to_be_translated():
		for lang in ['ar']:
			print("Writing for {0}-{1}".format(app, lang))
			path = os.path.join(frappe.get_app_path(app, "translations", lang + ".csv"))

			google_translations = []
			user_translations = []

			translations = get_translations_for_export(app, lang)

			for translation in translations:
				if translation.translated_by_google:
					google_translations.append(translation)
				else:
					user_translations.append(translation)

			parent = None
			parent_dict = {}
			if '-' in lang:
				# get translation from parent
				# for example es and es-GT
				parent = lang.split('-')[0]
				parent_dict = { t.source_text: t.translated_text for t in get_translations_for_export(app, parent) }

			with open(path, 'w') as msgfile:
				w = writer(msgfile, lineterminator='\n')
				for t in google_translations:
					# only write if translation is different from parent
					if not parent or (t.translated_text != parent_dict.get(t.source_text)):
						position = t.position or ''
						translated_text = strip(t.translated_text or '')
						context = strip(t.context or '')
						w.writerow([position, t.source_text.replace('\n', '\\n'), translated_text.replace('\n', '\\n'), context])
			make_a_commit(app, 'chore: Update translation')

			# commit per user translation
			for t in user_translations:
				if not parent or (t.translated_text != parent_dict.get(t.source_text)):
					position = t.position or ''
					translated_text = strip(t.translated_text or '')
					context = strip(t.context or '')
					with open(path, 'a') as msgfile:
						w = writer(msgfile, lineterminator='\n')
						w.writerow([position, t.source_text.replace('\n', '\\n'), translated_text.replace('\n', '\\n'), context])
					make_a_commit(app, 'chore: Update translation', t.contributor_name or t.modified_by, t.contributor_email or 'Verifier')


def get_translations_for_export(app, lang, only_untranslated_sources=False):
	# should return all translated text
	return frappe.db.sql("""
		SELECT
			source.name AS source_name,
			source.position AS position,
			source.message AS source_text,
			COALESCE(contributed.translated_string, translated.translated) AS translated_text,
			CASE WHEN contributed.translated_string IS NULL THEN 1 ELSE 0 END AS translated_by_google,
			contributed.context AS context,
			contributed.contributor_name,
			contributed.contributor_email,
			contributed.modified_by
		FROM `tabSource Message` source
			LEFT JOIN `tabTranslated Message` translated
				ON (source.name=translated.source AND translated.language = %(language)s)
			LEFT JOIN `tabContributed Translation` contributed
				ON (
					source.message=contributed.source_string
					AND contributed.language = %(language)s
					AND contributed.status = 'Verified'
					AND ((contributed.context IS NULL and contributed.context IS NULL) OR contributed.context=source.context)
				)
		WHERE
			source.disabled != 1 AND source.app = %(app)s
		GROUP BY
			source.message, source.context
		HAVING `translated_text` {} NULL
		ORDER BY
			source.creation
	""".format(
			'IS' if only_untranslated_sources else 'IS NOT'
		), dict(language=lang, app=app), as_dict=1)

def export_untranslated_to_json(lang, path):
	ret = {}
	for name, message in get_untranslated(lang):
		ret[name] = {
			"message": message.replace('$', '$$')
		}
	with open(path, 'wb') as f:
		json.dump(ret, f, indent=1)


def copy_translations(from_lang, to_lang):
	translations = frappe.db.sql("""select source, translated from `tabTranslated Message` where language=%s""", (from_lang, ))
	l = len(translations)
	for i, d in enumerate(translations):
		source, translated = d
		if not frappe.db.get_value('Translated Message', {"source": source, "language": to_lang}):
			t = frappe.new_doc('Translated Message')
			t.language = to_lang
			t.source = source
			t.translated = translated
			try:
				t.save()
			except frappe.ValidationError:
				pass

		update_progress_bar("Copying {0} to {1}".format(from_lang, to_lang), i, l)

def read_translation_csv_file(path):
	with open(path, 'rt') as f:
		reader = unicode_csv_reader(f)
		return list(reader)

def unicode_csv_reader(utf8_data, dialect=csv.excel, **kwargs):
	csv_reader = csv.reader(utf8_data, dialect=dialect, **kwargs)
	for row in csv_reader:
		yield [safe_decode(cell, 'utf-8') for cell in row]


def import_translations_from_csv(lang, path, modified_by='Administrator', if_older_than=None):
	translations = read_translation_csv_file(path)

	normalized_tranlations = []
	for translation in translations:
		if len(translation) == 2:
			normalized_tranlations.append(('', *translation, ''))
		elif len(translation) == 3:
			normalized_tranlations.append((*translation, ''))
		elif len(translation) == 4:
			normalized_tranlations.append(translation)

	count = 0
	print('importing', len(normalized_tranlations), 'translations')
	for pos, source_message, translated, context in normalized_tranlations:

		source_name = frappe.db.get_value("Source Message", {
			"message": source_message,
			"context": context
		})

		if not source_name:
			continue

		source = frappe.get_doc('Source Message', source_name)

		if source.disabled:
			continue

		dest = frappe.db.get_value("Translated Message", {
			"source": source_name,
			"language": lang
		})

		if dest:
			d = frappe.get_doc('Translated Message', dest)
			if if_older_than and d.modified > if_older_than:
				continue

			if d.modified_by != "Administrator" or d.translated != translated:
				frappe.db.set_value("Translated Message", dest, "translated", translated, modified_by=modified_by)
				count += 1
		else:
			dest = frappe.new_doc("Translated Message")
			dest.language = lang
			dest.translated = translated
			dest.source = source.name
			dest.save()
			count += 1
	print('updated', count)


def get_translation_from_google(lang, message):
	if lang == "cz":
		lang = "cs"
	s = frappe.utils.get_request_session()
	resp = s.get("https://www.googleapis.com/language/translate/v2", params={
		"key": frappe.conf.google_api_key,
		"source": "en",
		"target": lang,
		"q": message
	})
	resp.raise_for_status()
	return resp.json()["data"]["translations"][0]["translatedText"]

def translate_untranslated_from_google(lang):
	if lang == "en":
		return

	if lang=='zh-cn': lang = 'zh'
	if lang=='zh-tw': lang = 'zh-TW'

	if not get_lang_name(lang):
		print('{0} not supported by Google Translate'.format(lang))
		return

	count = 0
	untranslated = get_untranslated(lang)
	l = len(untranslated)

	for i, d in enumerate(untranslated):
		source, message = d
		if not frappe.db.get_value('Translated Message', {"source": source, "language": lang}):
			t = frappe.new_doc('Translated Message')
			t.language = lang
			t.source = source
			t.translated = get_translation_from_google(lang, message)
			try:
				t.save()
			except frappe.exceptions.ValidationError:
				continue
			count += 1
			frappe.db.commit()

		update_progress_bar("Translating {0}".format(lang), i, l)

	print(lang, count, 'imported')


def get_lang_name(lang):
	s = frappe.utils.get_request_session()
	resp = s.get("https://www.googleapis.com/language/translate/v2/languages", params={
		"key": frappe.conf.google_api_key,
		"target": "en"
	})

	languages = resp.json()['data']['languages']
	for l in languages:
		if l['language'] == lang:
			return l['name']

	return None

def get_untranslated(lang):
	source_messages = []
	for app in get_apps_to_be_translated():
		source_messages += get_translations_for_export(app, lang, True)

	untranslated_sources = [(s.source_name, s.source_text) for s in source_messages]
	return untranslated_sources

def get_apps_to_be_translated():
	return [d.name for d in frappe.db.get_all('Translator App')]

def make_a_commit(app, commit_msg, co_author_email=None, co_author_name=None):
	repo_path = os.path.join('/Users/sps/benches/develop-py3', 'apps', app)
	repo = Repo(repo_path)
	if co_author_email and co_author_name:
		commit_msg += "\n\nCo-authored-by: {} <{}>".format(co_author_name, co_author_email)

	repo.git.add('--all')
	return repo.index.commit(commit_msg)
