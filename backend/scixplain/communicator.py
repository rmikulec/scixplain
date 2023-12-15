from openai import AsyncOpenAI, OpenAI
import json
import pathlib
import wikipedia
import tiktoken

from scixplain import DEFAULT_MODEL
from scixplain.system_messages import DEFAULT, WIKI_SEARCH_TERMS
from scixplain.functions import WIKI_FUNCTIONS
from scixplain.datasources.wiki import WikiPage


class Communicator:
    def __init__(
        self,
        initial_question: str,
        age: int,
        experience: str,
        system_template: str = DEFAULT,
        max_tokens=300,
        n_pages: int = 1,
        n_sections: int = 3,
    ):
        self.client = OpenAI()
        self.max_tokens = max_tokens

        self.initial_question = initial_question
        self.age = age
        self.experience = experience
        self.pages = self._get_pages(question=initial_question, n_pages=n_pages)
        self.data = self._create_data()

        # Create system message
        self.system_message = system_template.format(
            data=self.data, age=self.age, experience=self.experience, n_sections=n_sections
        )
        self.system_message_n_tokens = self._get_num_tokens(self.system_message)

        if self.system_message_n_tokens >= 50_000:
            raise Exception(f"Too many tokens, try reducing pages: {self.system_message_n_tokens}")

        self.messages = [
            {"role": "system", "content": self.system_message},
            {"role": "user", "content": self.initial_question},
        ]

    def _get_wiki_search_terms(self, question):
        response = self.client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": WIKI_SEARCH_TERMS},
                {"role": "user", "content": question},
            ],
            max_tokens=50,
        )

        message = response.choices[0].message.content
        return json.loads(message)

    def _get_wikipedia_content(self, title, section):
        page = list(filter(lambda p: p.title == title, self.pages))[0]
        section_content = page.get_section_content(section)
        return json.dumps(section_content, indent=4)

    def _get_num_tokens(self, text):
        encoder = tiktoken.encoding_for_model(DEFAULT_MODEL)
        return len(encoder.encode(text))

    # TODO: Would like to update to do round robin addding.
    #   So take the first page from each term, then the second
    #   and so on
    # For now, just going to limit the terms instead of pages
    def _get_pages(self, question, n_pages=1):
        terms = self._get_wiki_search_terms(question)
        terms = list(set(terms))
        print(terms)

        potential_pages = [wikipedia.search(term)[0] for term in terms]

        potential_pages = list(set(potential_pages))
        results = potential_pages[0:n_pages]
        return [WikiPage(title=result) for result in results]

    def _create_data(self):
        data = []

        for page in self.pages:
            data.append(
                {
                    "title": page.title,
                    "images": page.image_captions,
                    "sections": list(page.indexed_content.keys()),
                    "url": page.url,
                    # "references": {i+1: ref for i, ref in enumerate(page.references)}
                }
            )

        return json.dumps(data, indent=4)

    def _call_openai(self):
        response = self.client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=self.messages,
            max_tokens=self.max_tokens,
            tools=WIKI_FUNCTIONS,
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        self.messages.append(response_message)

        if tool_calls:
            for tool_call in tool_calls:
                func_args = json.loads(tool_call.function.arguments)
                print(f"Getting section {func_args['section']} from {func_args['title']}")
                content = self._get_wikipedia_content(
                    title=func_args["title"], section=func_args["section"]
                )
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.function.name,
                    "content": content,
                }
                self.messages.append(tool_message)
            self._call_openai()

    def _export_results(self):
        last_message = self.messages[-1]

        if type(last_message) == dict:
            md = self.messages[-1]["content"]
        else:
            md = self.messages[-1].content
        root = pathlib.Path("./answers")
        root.mkdir(exist_ok=True, parents=True)
        (root / self.initial_question.lower().replace(" ", "-")).write_text(md)

    def run(self):
        self._call_openai()
        self._export_results()