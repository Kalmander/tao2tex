"""
tao2tex.py
by Calvin Khor

Goes through a saved HTML version of a wordpress math blogpost, and spits out a LaTeX version

A partial inverse for LaTeX2WP which can be found here:
https://lucatrevisan.wordpress.com/latex-to-wordpress/using-latex2wp/
This will work perfectly only for Tao's newer blogposts.

naming conventions:
    a formatter function returns a string,
    a wrapper function calls soup_processor or child_processor somewhere
    and returns a list of strings.

Typehints are just for readability; mypy complains a lot.
"""
import argparse
import datetime
import logging
import os
import re  # https://regexkit.com/python-regex

import emoji
import requests
from bs4 import (
    BeautifulSoup,
    FeatureNotFound,
    NavigableString,
    PageElement,
    SoupStrainer,
)

TIMEOUT_IN_SECONDS = 60
ASSUMED_DPI = 100


def html2soup(user_html: str, strainer: SoupStrainer) -> BeautifulSoup:
    """Creates a new soup from the raw html with an optional SoupStrainer."""
    try:
        soup = BeautifulSoup(user_html, "lxml", parse_only=strainer)
    except FeatureNotFound:
        logging.warning(
            "You should install the lxml parser: pip install lxml\n Trying with default parser"
        )
        soup = BeautifulSoup(user_html, parse_only=strainer)
    return soup


def download_file(url: str) -> str:
    """downloads a file at url; returns saved filename if successful, else an empty string"""
    url_simplifier = re.compile(r"(.*?)\?")
    if can_be_simplified := url_simplifier.search(url):
        url = can_be_simplified.group(1)
    filename_from_url = re.compile(r".*/(.*\..*)\?|.*/(.*\..*)")
    filename = url
    if filename_match := filename_from_url.match(url):
        if filename_match.group(1):
            filename = filename_match.group(1)
        filename = filename_match.group(2)
    if os.path.exists(url):
        # avoid redownloading files
        logging.debug("skipping download because file already exists")
        return filename
    try:
        raw_data = requests.get(url, timeout=TIMEOUT_IN_SECONDS)
    except requests.exceptions.ConnectionError:
        logging.warning("failed to download from url=%s", url)
        return ""

    with open(filename, "wb") as file:
        file.write(raw_data.content)
        return filename


def macro(
    macro_command: str,
    macro_input: str = "",
    macro_options: list[str] | None = None,
    options_before_input: bool = False,
) -> str:
    """simple command to make basic latex macros"""
    if macro_options:
        if options_before_input:
            return (
                "\\"
                + macro_command
                + "["
                + ",".join(macro_options)
                + "]"
                + "{"
                + macro_input
                + "}"
            )
        return (
            "\\"
            + macro_command
            + "{"
            + macro_input
            + "}"
            + "["
            + ",".join(macro_options)
            + "]"
        )
    return "\\" + macro_command + "{" + macro_input + "}"


def image_formatter(path: str, width: str, height: str) -> str:
    """formats an image at path using the includegraphics macro.
    (So, the preamble needs to include the graphicx package.)
    We assume pictures are at "ASSUMED_DPI" (dots per inch)"""

    options = []
    if width:
        width = int(width) / ASSUMED_DPI  # now in inches
        options.append(f"{width=} in")
    if height:
        height = int(height) / ASSUMED_DPI  # now in inches
        options.append(f"{height=} in")
    return (
        "\n\n"  # pictures are most likely meant to be on a new line.
        + macro("includegraphics", path, options, options_before_input=True)
        + "\n"
    )


def placeholder_formatter(width: str, height: str):
    """formats the default stock image using the includegraphics macro"""
    return image_formatter("example-image", width, height)


def ahref_formatter(href: str, text: str = "", use_raw_text: bool = False) -> str:
    """turns a href with only text into the corresponding LaTeX code.
    If no text is given, then the href is used as text."""
    text_formatter = (lambda t: t) if use_raw_text else string_formatter
    url_matcher = re.compile(r"(http|www).*")  # http or www, followed by anything
    ref_matcher = re.compile(r"[0-9]+")  # at least one number
    # at least one number in round brackets
    eqref_matcher = re.compile(r"\([0-9]+\)")

    if url_matcher.match(href):
        if text == "":
            text = string_formatter(href)
        return r"\href{" + string_formatter(href) + "}{" + text_formatter(text) + "}"
    elif href[0] == "#" and ref_matcher.match(text):
        return macro("ref", string_formatter(href[1:]))
    elif href[0] == "#" and eqref_matcher.match(text):
        return macro("eqref", string_formatter(href[1:]))
    else:
        return string_formatter(href)


def ahref_wrapper(href: str, soup: BeautifulSoup) -> list[str]:
    "figures out how to format soups that are wrapped by an a tag"
    soup_out = soup_processor(soup)
    # special case for images
    if len(soup.contents) == 1 and soup.contents[0].name == "img":
        return environment_formatter("center", ahref_formatter(href, "".join(soup_out)))
    return [ahref_formatter(href, "".join(soup_out), use_raw_text=True)]


def em_wrapper(soup: BeautifulSoup) -> list[str]:
    """formats a soup inside an <em> tag with the emph LaTeX macro."""
    return [macro("emph", "".join(soup_processor(soup)))]


def math_formatter(text: str, left_delim: str = r"\(", right_delim: str = r"\)") -> str:
    """adds math delimiters, hopefully around LaTeX formatted math text"""
    return left_delim + text + right_delim


def display_math_formatter(
    text: str, left_delim: str = r"\[", right_delim: str = r"\]"
) -> str:
    """adds display math delimiters, and removes \\displaystyle if present"""
    displaystyle_matcher = re.compile(r"(?:\\displaystyle)? *(.*)")
    if displaystyle_match := displaystyle_matcher.match(text):
        text = displaystyle_match.group(1)
    return math_formatter(text, left_delim, right_delim)


def labelled_math_formatter(text: str, label: str, env_type: str = "align") -> str:
    """Formats labelled display math. On Tao's blogs,
    the equation number is hard-coded in. So we need to remove it"""
    extra_eqno_matcher = re.compile(r"(?:\\displaystyle)?(.*?)(?:\\ )+\([0-9]+\)")
    if number_match := extra_eqno_matcher.match(text):
        text = number_match.group(1)
        left_delim = r"\begin{" + env_type + "}" + label_formatter(label)
        right_delim = r"\end{" + env_type + "}"
    return math_formatter(text, left_delim, right_delim)


def section_formatter(text: str) -> str:
    """formats a section header using the section LaTeX macro.
    implementations are probably highly different across blogs so we just hardcode Tao's in:
    this means we search for and remove leading numbers."""
    section_matcher = re.compile(r"[a-zA-Z,]+")
    if section_match := section_matcher.findall(text):
        text = string_formatter(" ".join(section_match))
    return macro("section", text)


def environment_formatter(env_type: str, text: str, options: list[str] = None) -> str:
    """wraps text in an environment"""
    return macro("begin", env_type, options) + text + macro("end", env_type)


def environment_wrapper(
    env_type: str, soup: BeautifulSoup, options: list[str] = None
) -> list[str]:
    """processes and wraps a soup in an environment"""
    return (
        macro("begin", env_type, options)
        + "".join(soup_processor(soup))
        + macro("end", env_type)
    )


def theorem_wrapper(unprocessed_thm_title: str, soup: BeautifulSoup) -> list[str]:
    """formats a blockquote into a theorem/conjecture/etc environment"""
    theoremtype = "note"  # randomly defaulting to "note" to avoid latex errors
    # first word in unprocessed_thm_title
    title_matcher = re.compile(r"([a-zA-z]*) ")
    if title_match := re.search(title_matcher, unprocessed_thm_title):
        match title := title_match.group(1).lower():
            case (
                "exercise"
                | "theorem"
                | "corollary"
                | "example"
                | "remark"
                | "conjecture"
                | "proposition"
                | "lemma"
                | "definition"
                | "note"
            ):
                theoremtype = title

    options = []
    options_matcher = re.compile(
        r"\((.*)?\)"
    )  # look for a pair of brackets in unprocessed_thm_title
    if options_match := re.search(options_matcher, unprocessed_thm_title):
        options = [options_match.group(1)]
    return environment_wrapper(theoremtype, soup, options)


def label_formatter(label: str) -> str:
    "formats a label as a LaTeX command"
    return macro("label", label)


def string_formatter(text: str) -> str:
    """Escapes special LaTeX characters and unusual whitespaces (sorry foreign languages).
    Hence this should not be called in math_formatter and related functions."""
    prefix = " " if text.startswith(" ") else ""
    postfix = " " if text.endswith(" ") else ""
    text = prefix + " ".join(text.split()) + postfix
    # there must be better syntax for the below...
    unusual_whitespace = (
        "\u0009\u00A0\u00AD\u034F\u061c\u115f\u1160\u17b4\u17b5\u180e"
        + "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009"
        + "\u200A\u200B\u200C\u200D\u200E\u200F\u202F"
        + "\u205F\u2060\u2061\u2062\u2063\u2064\u206A\u206b\u206c"
        + "\u206d\u206e\u206f\u3000\u2800\u3164\ufeff\uffa0\U0001D159"
        + "\U0001D173\U0001D174\U0001D175\U0001D176\U0001D177\U0001D178\U0001D179\U0001D17A"
    )
    whitespace_regex = re.compile("[" + unusual_whitespace + "]+")
    text = re.sub(whitespace_regex, " ", text)
    other_substitutions = str.maketrans(
        {  # LaTeX doesn't like these chars
            "\uff0c": ",",  # U+FF0C = "full-width comma"             '，'.
            "\u3002": ".",  # U+3002 = "ideographic full stop"        '。'.
            "\uff1a": ":",  # U+FF1A = "full-width colon"             '：'.
            "\uff1b": ";",  # U+FF1B = "full-width semicolon"         '；'.
            "\uff08": "(",  # U+FF08 = "full-width opening bracket"   '（'.
            "\uff09": ")",  # U+FF09 = "full-width closing bracket"   '）'.
            "\uff01": "!",  # U+FF01 = "full-width exclamation point" '！'
            "\\": r"\textbackslash{}",
            r"^": r"\textasciicircum{}",
            "#": r"\#",
            "~": r"\textasciitilde{}",
            "|": r"\textbar{}",
            "$": r"\$",
            "%": r"\%",
            "&": r"\&",
            "_": r"\_",
            r"{": r"\{",
            r"}": r"\}",
        }
    )

    text = text.translate(other_substitutions)

    # finally we need to use the emoji module to convert emojis
    # into something that LaTeX can handle. We put them into an \emoji macro;
    #   either compile with \usepackage{emoji} in LuaTeX,
    #   or use the default definition of \emoji in preamble.tex.
    text = emoji.replace_emoji(
        text,
        replace=lambda chars, data_dict: macro(
            "emoji", data_dict["en"].strip(":").replace("_", "-")
        ),
    )

    return text


def ol_wrapper(soup: BeautifulSoup) -> list[str]:
    """turns ol tags into enumerates"""
    return environment_wrapper("enumerate", soup)


def ul_wrapper(soup: BeautifulSoup) -> list[str]:
    """turns ul tags into itemizes"""
    return environment_wrapper("itemize", soup)


def li_wrapper(soup: BeautifulSoup, find_bullet: bool = True) -> list[str]:
    """adds an item command before continuing to process the soup.
    Attempts to detect if a custom bullet was manually typed and use that instead."""
    bullet_option = ""
    if (
        find_bullet
        and len(soup.contents) > 0
        and (first_child := soup.contents[0])
        and isinstance(first_child, NavigableString)
    ):
        first_child = str(first_child.extract())
        bullet_matcher = re.compile(r"(?:[\(\[]?[0-9ivxIabcABC]?\w?\w[\)\]\:.])|[\*-]")
        #  see  https://regexkit.com/python-regex,
        # matches common bullet or numberings
        # eg: "1.", "(2)", "[3]", "4)", "(v)", "*", and "."
        # Doesn't match 1, because it will then match e.g "Therefore,".
        # only matches ≤3 chars in label to avoid e.g. (Diffusion)
        # Doesn't match Eg. 1, Eg. 2, Eg. 3...

        if bullet_match := bullet_matcher.match(first_child):
            bullet_option = "[" + bullet_match.group() + "]"
            first_child = bullet_matcher.sub("", first_child, count=1)

        return [r"\item " + bullet_option, first_child] + soup_processor(soup)
    # fallback
    return [r"\item "] + soup_processor(soup)


def table_wrapper(soup: BeautifulSoup):
    """Formats a table using the tabular environment"""
    out = []
    table_length = 0
    for child in soup.children:
        if child.name == "tr" or child.name == "th":

            row = []
            for gchild in child.children:
                if (
                    isinstance(gchild, NavigableString)
                    and gchild.get_text().strip() == ""
                ):
                    continue
                row.append("".join(soup_processor(gchild)))
            table_length = max(table_length, len(row))
            out.append("&".join(row))
            out.append(r"\\")

    column_width = 0.9 / table_length
    column_format = "p{" + str(column_width) + "\\linewidth} "
    beginning_string = (
        macro("begin", "tabular") + "{" + column_format * table_length + "}"
    )
    ending_string = macro("end", "tabular")
    return environment_formatter(
        "center", "".join([beginning_string] + out + [ending_string])
    )


def strike_wrapper(child: PageElement):
    """Formats a strikethrough"""
    return macro("sout", "".join(soup_processor(child)))


def child_processor(child: PageElement) -> list[str]:
    """Turns a child element into a list of legal LaTeX strings.
    We return a list instead of a single string to enable something like mild recursion.
    Unfortunately this is all just heuristics.
    Code is arranged to attempt to split
        - detecting what and where LaTeX commands are required (which happens here)
        - how the command should be typed (formatters and wrappers)
    """
    logging.debug("processing child=%s", child)
    if not child:
        logging.warning("empty child in child_processor")
        return []
    if isinstance(child, NavigableString):
        return [string_formatter(child.get_text())]

    elif child.name == "em" or child.name == "i":
        return em_wrapper(child)

    elif child.name == "br":
        return ["\n"]

    elif child.name == "table":
        return table_wrapper(child)

    elif child.name == "p" and (
        "align" in child.attrs.keys()
        or ("style" in child.attrs.keys() and "text-align:center;" in child["style"])
    ):
        extra_string = ""
        for grandchild in child.children:
            if isinstance(grandchild, NavigableString):
                extra_string += grandchild.get_text()
        if extra_string != "":
            extra_string = r"\qquad" + extra_string
        if (
            child.contents[0].name == "img"
            and "alt" in child.contents[0].attrs.keys()
            and "class" in child.contents[0].attrs.keys()
            and child.contents[0]["class"] == ["latex"]
        ):
            return [display_math_formatter(child.contents[0]["alt"] + extra_string)]
        if (
            child.contents[0].name == "a"
            and child.contents[1].name == "img"
            and "class" in child.contents[1].attrs.keys()
            and child.contents[1]["class"] == ["latex"]
        ):
            # this may break if the case handling <a name="..."> below is changed.
            # specifically, we place the <a name="..."> at the beginning of the p tag.
            return [
                labelled_math_formatter(
                    child.contents[1]["alt"], child.contents[0]["name"]
                )
            ]

        elif child.contents[0].name == "b":
            return [section_formatter(child.contents[0].get_text())]
        else:
            # fallback processing.
            logging.warning(
                'fallback to basic processing in p align="..." tag\n child=%s',
                str(child),
            )
            return soup_processor(child)
    elif (
        child.name == "img"
        and "alt" in child.attrs.keys()
        and "class" in child.attrs.keys()
        and child["class"] == ["latex"]
    ):
        return [math_formatter(child["alt"])]
    elif child.name == "img":
        if "src" in child.attrs.keys():
            src = child["src"]
            width = ""
            height = ""
            if "width" in child.attrs.keys():
                width = child["width"]
            if "height" in child.attrs.keys():
                height = child["height"]
            if filename := download_file(src):
                return [image_formatter(filename, width, height)]
            return [placeholder_formatter(width, height)]

        logging.warning("img tag with no src attr: child=%s", str(child))
        return []

    elif child.name == "a" and "href" in child.attrs.keys():
        for grandchild in child.children:
            if not isinstance(grandchild, NavigableString) and not isinstance(
                grandchild, str
            ):
                return ahref_wrapper(child["href"], child)
        return [ahref_formatter(child["href"], child.get_text())]
    elif child.name == "a" and "name" in child.attrs.keys():
        # In LaTeX, labels need to appear inside of the environment it labels.
        # We move this into the heuristically determined correct environment and defer processing
        # this until processing <p align="..."> tags.
        # if this ever breaks, good luck whoever wants to debug this in the future...
        if child.contents:
            for gchild in child.contents:
                if (
                    isinstance(gchild, NavigableString)
                    and gchild.get_text().strip() == ""
                ):
                    continue
                if gchild.name == "p" and gchild.contents[0].name == "img":
                    return [
                        labelled_math_formatter(
                            gchild.contents[0]["alt"], child["name"]
                        )
                    ]
        elif (
            (parent := child.parent)
            and parent.name == "p"
            and "align" not in parent.attrs.keys()
            and parent.contents[-1] == child
            and (second_uncle := parent.next_sibling.next_sibling)
            and second_uncle.name == "p"
            and "align" in second_uncle.attrs.keys()
        ):
            # make second_uncle adopt child
            second_uncle.insert(0, child)
            # we skip formatting now, as it will be formatted when
            # we reach the second_uncle in the outermost for loop.
            return []
        return [label_formatter(child.attrs["name"])]
    elif child.name == "blockquote":
        if child.b:
            unprocessed_thm_name = (
                child.b.extract().get_text()
            )  # NB extract() removes the tag so that it is not processed twice.
        elif child.p and child.p.b:
            unprocessed_thm_name = (
                child.p.b.extract().get_text()
            )  # NB extract() removes the tag so that it is not processed twice.
        else:
            logging.debug(
                "unknown theorem: will use theorem_wrapper's default\nchild=%s",
                str(child),
            )
            unprocessed_thm_name = ""
        return theorem_wrapper(unprocessed_thm_name, child)
    elif child.name == "p":
        return soup_processor(child) + ["\n"]
    elif child.name == "ul":
        return ul_wrapper(child)
    elif child.name == "ol":
        return ol_wrapper(child)
    elif child.name == "li":
        return li_wrapper(child)
    elif child.name == "div" and (
        ("class" in child.attrs.keys() and "sharedaddy" in child.attrs["class"][0])
        or ("class" in child.attrs.keys() and "cs-rating" in child.attrs["class"])
        or ("id" in child.attrs.keys() and "jp-post-flair" in child.attrs["id"])
    ):
        return []
    elif child.name == "strike":
        return strike_wrapper(child)
    elif child.name == "span" and len(child.contents) == 0:
        return []
    else:
        # fallback to get_text
        logging.warning("unknown tag: child=%s", str(child))
        return [child.get_text()]


def soup_processor(soup: BeautifulSoup) -> list[str]:
    """A simple loop on child_processor that converts a BeautifulSoup
    into a list of legal LaTeX strings."""
    out = []
    if not soup:
        logging.warning("empty soup in soup_processor")
        return out
    for child in soup.children:
        out.extend(child_processor(child))

    return out


def preamble_formatter(
    template_filename: str,
    blog_title: str,
    tagline: str,
    title: str,
    metadata: str,
    signature: str,
) -> str:
    """spit out a preamble as a long string, using the template"""
    # if you don't escape the slahes, the regex will not work
    slash_escaper = re.compile(r"\\")

    # I don't really have a good way to arrange the variables below
    # so if you want to change this, you need to change it three times
    # twice below and in the preamble
    template_vars = {
        "BLOG-TITLE": blog_title,
        "TAGLINE": tagline,
        "TITLE": title,
        "METADATA": metadata,
        "SIGNATURE": signature,
    }
    with open(template_filename, "r", encoding="UTF-8") as template:
        out = template.read()
        for var in template_vars:
            template_vars[var] = slash_escaper.sub(r"\\\\", template_vars[var])
            var_matcher = re.compile("TTT-" + var)
            out = var_matcher.sub(template_vars[var], out)
        return out


def comments_section_processor(comments_soup: BeautifulSoup) -> list[str]:
    """Converts the soup into a comments section,
    with a helper function comments_section_processor1 which deals with
    organizing the comments themselves.

    This helper calls comment_processor which formats a single comment."""
    comments_title = " no title "
    comments = [macro("begin", "itemize")]
    for child in comments_soup.children:
        # pull out section title and call comment_processor
        if (
            child.name == "div"
            and "id" in child.attrs.keys()
            and child["id"] == "comments-meta"
        ):
            for gchild in child.children:
                if isinstance(gchild, NavigableString):
                    comments.append(string_formatter(str(gchild)))
                elif (
                    "class" in gchild.attrs.keys()
                    and "comments-title" in gchild["class"]
                ):
                    comments_title = macro(
                        "section*", string_formatter(gchild.get_text())
                    )

        else:
            comments.extend(comments_section_processor1(child))

    return [comments_title] + comments + [macro("end", "itemize")]


def comments_section_processor1(child: BeautifulSoup, depth: int = 0) -> list[str]:
    """helper function to allow nested comments (i.e. replies) via recursion.
    Actual formatting of comments is done in another helper, comment_processor"""
    comments = []
    if (
        child.name == "div"
        and "class" in child.attrs.keys()
        and "comment" in child.attrs["class"]
    ):
        comments.append(comment_processor(child))
    elif child.name == "ul":
        if depth < 3:
            comments.append(macro("begin", "itemize"))
        for gchild in child.children:
            comments.extend(comments_section_processor1(gchild, depth + 1))
        if depth < 3:
            comments.append(macro("end", "itemize"))
    return comments


def comment_processor(soup: BeautifulSoup) -> list[str]:
    """get for each comment: author name, date, and the comment string.
    The first string in the output is the timestamp.
    The second string is  the author name.
    The remainder is the comment string.
    """
    timestamp = "unknown"
    author = "unknown"
    comment = []
    for child in soup.children:
        if isinstance(child, NavigableString):
            continue
        if "class" in child.attrs.keys() and "comment-metadata" in child.attrs["class"]:
            for gchild in child.children:
                if isinstance(gchild, NavigableString):
                    continue
                if (
                    "class" in gchild.attrs.keys()
                    and "comment-author" in gchild.attrs["class"]
                ):
                    author = string_formatter(gchild.get_text())
                elif (
                    "class" in gchild.attrs.keys()
                    and "comment-permalink" in gchild.attrs["class"]
                ):
                    timestamp = string_formatter(gchild.get_text())

        elif (
            "class" in child.attrs.keys() and "comment-content" in child.attrs["class"]
        ):
            for gchild in child.children:
                if isinstance(gchild, NavigableString):
                    continue
                if gchild.name == "img":
                    continue  # Let's not process the avatars.
                comment += child_processor(gchild)
    return (
        macro("item", "")
        + macro("textbf", author + macro("hfill", "") + timestamp)
        + r"\\"
        + "".join(comment)
        + "\n"
    )


def url2tex(
    url: str,
    local: bool,
    output: str,
    print_output: bool = False,
    save_url: bool = False,
):
    "opens a url (or file) and creates a tex file with name given by output"
    raw_html = ""
    if local:
        with open(url, "r", encoding="UTF-8") as html_doc:
            raw_html = html_doc.read()
    else:
        raw_html = requests.get(url, timeout=TIMEOUT_IN_SECONDS).text

    signature = (
        r"Automatically generated  using "
        + ahref_formatter("https://github.com/clvnkhr/tao2tex", "tao2tex.py")
        + f" from {ahref_formatter(url)} at {datetime.datetime.now()}"
    )

    header_strainer = SoupStrainer("div", id="header")
    blog_title = "Blog Title Goes Here"
    header_soup = html2soup(raw_html, header_strainer)
    if may_have_title := header_soup.find(id="blog-title"):
        blog_title = string_formatter(may_have_title.get_text())
    elif may_have_title := header_soup.find(id="title"):
        blog_title = string_formatter(may_have_title.get_text())
    else:
        # take the title from the <head> tag
        every_page_has_a_title = SoupStrainer("head")
        blog_title = soup_processor(html2soup(raw_html, every_page_has_a_title))

    tagline = "Blog Tagline Goes Here"
    if may_have_tagline := header_soup.find(id="tagline"):
        tagline = string_formatter(may_have_tagline.get_text())

    primary_strainer = SoupStrainer("div", id="primary")
    primary_soup = html2soup(raw_html, primary_strainer)

    title = "Post Title Goes Here"
    if may_be_post_title := primary_soup.h1:
        title = string_formatter(may_be_post_title.get_text())
    elif may_be_post_title := primary_soup.find("title"):
        title = string_formatter(may_be_post_title.get_text())
    else:
        title = blog_title

    metadata = soup_processor(primary_soup.find("p", "post-metadata"))
    metadata = "".join(metadata)

    comment_strainer = SoupStrainer("div", id="comments")
    comment_soup = html2soup(raw_html, comment_strainer)
    preamble = preamble_formatter(
        template_filename="preamble.tex",
        blog_title=blog_title,
        tagline=tagline,
        title=title,
        metadata=metadata,
        signature=signature,
    )

    content = primary_soup.find(attrs={"class": "post-content"})
    if not content:
        content = primary_soup.find(attrs={"class": "content"})

    comments = comment_soup.find(attrs={"id": "comments"})
    if not comments:
        comments = comment_soup.find(attrs={"id": "commentslist"})

    out = (
        [preamble, r"\begin{document}", r"\maketitle{}"]
        + soup_processor(content)
        + comments_section_processor(comments)
        + [r"\end{document}"]
    )
    if not output:
        output = blog_title + "-" + title
    with open(output + ".tex", "w", encoding="utf-8") as output_file:
        output_file.write("".join(out))
    if print_output:
        print("".join(out))
    if save_url:
        with open(output + ".html", "w", encoding="utf-8") as output_file:
            output_file.write("".join(out))

    logging.debug("the output is %i lines long.", len(out))


def main():
    """parses the command line arguments and passes them to url2tex"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-b", "--batch", help="batch process files", action="store_true"
    )
    parser.add_argument(
        "-l", "--local", help="treat url as a local file", action="store_true"
    )
    parser.add_argument("url", help="url of blog post to convert")
    parser.add_argument("-o", "--output", help="name of output file")
    parser.add_argument(
        "-p", "--print", help="print output to command line", action="store_true"
    )
    parser.add_argument(
        "-d", "--debug", help="Log debug statements", action="store_true"
    )

    parser.add_argument(
        "--save-url", help="save the html to a .html file", action="store_true"
    )

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(filename="tao2tex_debug.log", level=logging.DEBUG)

    if args.batch:
        with open(args.url, "r", encoding="utf8") as file:
            list_of_filenames = file.readlines()
            for i, filename in enumerate(list_of_filenames):
                numbered_name = None
                if args.output:
                    numbered_name = args.output + str(i)
                url2tex(filename.strip(), args.local, numbered_name)
    else:
        url2tex(args.url, args.local, args.output, args.print, args.save_url)


if __name__ == "__main__":
    main()
