"""Catalog of every language the multilingual Whisper models can transcribe.

Nothing is downloaded per language: one multilingual model (e.g. `small`)
covers all of these. "Adding" a language in local-flow only makes it
selectable in the menu and assignable to a quick key.

code → (native name, English name)
"""

CATALOG = {
    "af": ("Afrikaans", "Afrikaans"),
    "am": ("አማርኛ", "Amharic"),
    "ar": ("العربية", "Arabic"),
    "as": ("অসমীয়া", "Assamese"),
    "az": ("Azərbaycan", "Azerbaijani"),
    "ba": ("Башҡорт", "Bashkir"),
    "be": ("Беларуская", "Belarusian"),
    "bg": ("Български", "Bulgarian"),
    "bn": ("বাংলা", "Bengali"),
    "bo": ("བོད་སྐད་", "Tibetan"),
    "br": ("Brezhoneg", "Breton"),
    "bs": ("Bosanski", "Bosnian"),
    "ca": ("Català", "Catalan"),
    "cs": ("Čeština", "Czech"),
    "cy": ("Cymraeg", "Welsh"),
    "da": ("Dansk", "Danish"),
    "de": ("Deutsch", "German"),
    "el": ("Ελληνικά", "Greek"),
    "en": ("English", "English"),
    "es": ("Español", "Spanish"),
    "et": ("Eesti", "Estonian"),
    "eu": ("Euskara", "Basque"),
    "fa": ("فارسی", "Persian"),
    "fi": ("Suomi", "Finnish"),
    "fo": ("Føroyskt", "Faroese"),
    "fr": ("Français", "French"),
    "gl": ("Galego", "Galician"),
    "gu": ("ગુજરાતી", "Gujarati"),
    "ha": ("Hausa", "Hausa"),
    "haw": ("ʻŌlelo Hawaiʻi", "Hawaiian"),
    "he": ("עברית", "Hebrew"),
    "hi": ("हिन्दी", "Hindi"),
    "hr": ("Hrvatski", "Croatian"),
    "ht": ("Kreyòl ayisyen", "Haitian Creole"),
    "hu": ("Magyar", "Hungarian"),
    "hy": ("Հայերեն", "Armenian"),
    "id": ("Bahasa Indonesia", "Indonesian"),
    "is": ("Íslenska", "Icelandic"),
    "it": ("Italiano", "Italian"),
    "ja": ("日本語", "Japanese"),
    "jw": ("Basa Jawa", "Javanese"),
    "ka": ("ქართული", "Georgian"),
    "kk": ("Қазақ", "Kazakh"),
    "km": ("ខ្មែរ", "Khmer"),
    "kn": ("ಕನ್ನಡ", "Kannada"),
    "ko": ("한국어", "Korean"),
    "la": ("Latina", "Latin"),
    "lb": ("Lëtzebuergesch", "Luxembourgish"),
    "ln": ("Lingála", "Lingala"),
    "lo": ("ລາວ", "Lao"),
    "lt": ("Lietuvių", "Lithuanian"),
    "lv": ("Latviešu", "Latvian"),
    "mg": ("Malagasy", "Malagasy"),
    "mi": ("Te Reo Māori", "Maori"),
    "mk": ("Македонски", "Macedonian"),
    "ml": ("മലയാളം", "Malayalam"),
    "mn": ("Монгол", "Mongolian"),
    "mr": ("मराठी", "Marathi"),
    "ms": ("Bahasa Melayu", "Malay"),
    "mt": ("Malti", "Maltese"),
    "my": ("မြန်မာ", "Burmese"),
    "ne": ("नेपाली", "Nepali"),
    "nl": ("Nederlands", "Dutch"),
    "nn": ("Nynorsk", "Norwegian Nynorsk"),
    "no": ("Norsk", "Norwegian"),
    "oc": ("Occitan", "Occitan"),
    "pa": ("ਪੰਜਾਬੀ", "Punjabi"),
    "pl": ("Polski", "Polish"),
    "ps": ("پښتو", "Pashto"),
    "pt": ("Português", "Portuguese"),
    "ro": ("Română", "Romanian"),
    "ru": ("Русский", "Russian"),
    "sa": ("संस्कृतम्", "Sanskrit"),
    "sd": ("سنڌي", "Sindhi"),
    "si": ("සිංහල", "Sinhala"),
    "sk": ("Slovenčina", "Slovak"),
    "sl": ("Slovenščina", "Slovenian"),
    "sn": ("chiShona", "Shona"),
    "so": ("Soomaali", "Somali"),
    "sq": ("Shqip", "Albanian"),
    "sr": ("Српски", "Serbian"),
    "su": ("Basa Sunda", "Sundanese"),
    "sv": ("Svenska", "Swedish"),
    "sw": ("Kiswahili", "Swahili"),
    "ta": ("தமிழ்", "Tamil"),
    "te": ("తెలుగు", "Telugu"),
    "tg": ("Тоҷикӣ", "Tajik"),
    "th": ("ไทย", "Thai"),
    "tk": ("Türkmen", "Turkmen"),
    "tl": ("Tagalog", "Tagalog"),
    "tr": ("Türkçe", "Turkish"),
    "tt": ("Татар", "Tatar"),
    "uk": ("Українська", "Ukrainian"),
    "ur": ("اردو", "Urdu"),
    "uz": ("Oʻzbek", "Uzbek"),
    "vi": ("Tiếng Việt", "Vietnamese"),
    "yi": ("ייִדיש", "Yiddish"),
    "yo": ("Yorùbá", "Yoruba"),
    "zh": ("中文", "Chinese"),
}


def native_name(code: str) -> str:
    return CATALOG.get(code, (code, code))[0]


def english_name(code: str) -> str:
    return CATALOG.get(code, (code, code))[1]


def label(code: str) -> str:
    """Menu label: native name, with the English name when it differs."""
    native, english = CATALOG.get(code, (code, code))
    return native if native == english else f"{native} ({english})"


def sorted_codes():
    """All codes ordered by English name, for pickers."""
    return sorted(CATALOG, key=lambda c: CATALOG[c][1].lower())
