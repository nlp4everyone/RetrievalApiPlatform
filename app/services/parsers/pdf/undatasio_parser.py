# Inherit
from ..base import BaseTextParser
# Main component
from langchain_undatasio import UnDatasIOLoader

class UnDatasIOPDFParser(BaseTextParser):
    def __init__(self,
                 api_key :str):
        self._api_key = api_key

    async def parse(self,
                    file_input :str) -> str:
        # Define Parser
        self._parser = UnDatasIOLoader(token = self._api_key,
                                       file_path = file_input)

        # Get content by pages
        return "\n".join([doc.page_content async for doc in self._parser.alazy_load()])