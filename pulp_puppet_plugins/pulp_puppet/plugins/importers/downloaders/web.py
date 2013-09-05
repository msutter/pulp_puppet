# -*- coding: utf-8 -*-
#
# Copyright © 2012 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public
# License as published by the Free Software Foundation; either version
# 2 of the License (GPLv2) or (at your option) any later version.
# There is NO WARRANTY for this software, express or implied,
# including the implied warranties of MERCHANTABILITY,
# NON-INFRINGEMENT, or FITNESS FOR A PARTICULAR PURPOSE. You should
# have received a copy of GPLv2 along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.

import copy
import logging
import os
from StringIO import StringIO

from nectar.downloaders.threaded import HTTPThreadedDownloader
from nectar.listener import DownloadEventListener
from nectar.request import DownloadRequest

from pulp.plugins.util.nectar_config import importer_config_to_nectar_config

from pulp_puppet.plugins.importers.downloaders.base import BaseDownloader
from pulp_puppet.common import constants
from pulp_puppet.plugins.importers.downloaders import exceptions

# -- constants ----------------------------------------------------------------

DOWNLOAD_TMP_DIR = 'http-downloads'

_LOG = logging.getLogger(__name__)

# -- downloader implementations -----------------------------------------------

class HttpDownloader(BaseDownloader):
    """
    Used when the source for puppet modules is a remote source over HTTP.
    """

    def retrieve_metadata(self, progress_report):

        urls = self._create_metadata_download_urls()

        # Update the progress report to reflect the number of queries it will take
        progress_report.metadata_query_finished_count = 0
        progress_report.metadata_query_total_count = len(urls)

        config = importer_config_to_nectar_config(self.config.flatten())
        listener = HTTPMetadataDownloadEventListener(progress_report)
        self.downloader = HTTPThreadedDownloader(config, listener)

        request_list = [DownloadRequest(url, StringIO()) for url in urls]

        # Let any exceptions from this bubble up, the caller will update
        # the progress report as necessary
        try:
            self.downloader.download(request_list)

        finally:
            self.downloader = None

        return [r.destination.getvalue() for r in request_list]

    def retrieve_module(self, progress_report, module):
        return self.retrieve_modules(progress_report, [module])

    def retrieve_modules(self, progress_report, module_list):

        if self.downloader is None:
            config = importer_config_to_nectar_config(self.config.flatten())
            listener = None
            self.downloader = HTTPThreadedDownloader(config, listener)

        request_list = []

        for module in module_list:
            url = self._create_module_url(module)
            module_tmp_dir = _create_download_tmp_dir(self.repo.working_dir)
            module_tmp_filename = os.path.join(module_tmp_dir, module.filename())
            request = DownloadRequest(url, module_tmp_filename)
            request_list.append(request)

        self.downloader.download(request_list)

        return [r.destination for r in request_list]

    def cancel(self, progress_report):
        downloader = self.downloader
        if downloader is None:
            return
        downloader.cancel()

    def cleanup_module(self, module):

        module_tmp_dir = _create_download_tmp_dir(self.repo.working_dir)
        module_tmp_filename = os.path.join(module_tmp_dir, module.filename())

        if os.path.exists(module_tmp_filename):
            os.remove(module_tmp_filename)

    def _create_metadata_download_urls(self):
        """
        Uses the plugin configuration to determine a list of URLs for all
        metadata documents that should be used in the sync.

        :return: list of URLs to be downloaded
        :rtype:  list
        """
        feed = self.config.get(constants.CONFIG_FEED)
        # Puppet forge is sensitive about a double slash, so strip the trailing here
        if feed.endswith('/'):
            feed = feed[:-1]
        base_url = feed + '/' + constants.REPO_METADATA_FILENAME

        all_urls = []

        queries = self.config.get(constants.CONFIG_QUERIES)
        if queries:
            for query in queries:
                query_url = copy.copy(base_url)
                query_url += '?'

                # The config supports either single queries or tuples of them.
                # If it's a single, wrap it in a list so we can handle them the same
                if not isinstance(query, (list, tuple)):
                    query = [query]

                for query_term in query:
                    query_url += 'q=%s&' % query_term

                # Chop off the last & that was added
                query_url = query_url[:-1]
                all_urls.append(query_url)
        else:
            all_urls.append(base_url)

        return all_urls

    def _create_module_url(self, module):
        """
        Generates the URL for a module at the configured source.

        :param module: module instance being downloaded
        :type  module: pulp_puppet.common.model.Module

        :return: full URL to download the module
        :rtype:  str
        """
        url = self.config.get(constants.CONFIG_FEED)
        if not url.endswith('/'):
            url += '/'

        url += constants.HOSTED_MODULE_FILE_RELATIVE_PATH % (module.author[0], module.author)
        url += module.filename()
        return url

# -- private classes ----------------------------------------------------------

class HTTPMetadataDownloadEventListener(DownloadEventListener):

    def __init__(self, progress_report):
        self.progress_report = progress_report

    def download_started(self, report):
        self.progress_report.metadata_current_query = report.url
        self.progress_report.update_progress()

    def download_succeeded(self, report):
        self.progress_report.metadata_query_finished_count += 1
        self.progress_report.update_progress()

    def download_failed(self, report):
        raise exceptions.FileRetrievalException(report.error_msg)


class HTTPModuleDownloadEventListener(DownloadEventListener):

    def __init__(self, progress_report):
        self.progress_report = progress_report

    def download_started(self, report):
        pass

    def download_succeeded(self, report):
        pass

    def download_failed(self, report):
        raise exceptions.FileRetrievalException(report.error_msg)

# -- utilities ----------------------------------------------------------------

def _create_download_tmp_dir(repo_working_dir):
    tmp_dir = os.path.join(repo_working_dir, DOWNLOAD_TMP_DIR)
    if not os.path.exists(tmp_dir):
        os.mkdir(tmp_dir)
    return tmp_dir
