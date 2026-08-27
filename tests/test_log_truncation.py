import logging
import os
import tempfile
import unittest

from app import TruncatingFileHandler


class TruncatingFileHandlerTest(unittest.TestCase):
    def test_file_is_cleared_at_limit_without_backup(self):
        handle, path = tempfile.mkstemp(suffix='.log')
        os.close(handle)
        logger = logging.getLogger(f'truncation-test-{id(self)}')
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = TruncatingFileHandler(
            path, maxBytes=256, backupCount=0, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
        try:
            logger.info('OLD-' + ('x' * 180))
            logger.info('NEW-' + ('y' * 180))
            logger.info('LAST')
            handler.flush()

            with open(path, encoding='utf-8') as log_file:
                content = log_file.read()
            self.assertLessEqual(os.path.getsize(path), 256)
            self.assertNotIn('OLD-', content)
            self.assertIn('NEW-', content)
            self.assertIn('LAST', content)
            self.assertFalse(os.path.exists(path + '.1'))
        finally:
            logger.removeHandler(handler)
            handler.close()
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
