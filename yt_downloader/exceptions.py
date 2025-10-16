class DownloaderException(Exception):
    """Base exception for downloader errors"""
    pass


class DownloadError(DownloaderException):
    """Error downloading video"""
    pass


class ConfigError(DownloaderException):
    """Downloader configuration error"""
    pass


class DirectoryError(DownloaderException):
    """Error creating or accessing directory"""
    pass
