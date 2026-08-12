const { decryptCredential, encryptCredential } = require('../credential-crypto');

const [command, key, payload = '', context = ''] = process.argv.slice(2);
if (command === 'encrypt') process.stdout.write(encryptCredential(payload, key, context));
else if (command === 'decrypt') process.stdout.write(decryptCredential(payload, key, context));
else process.exitCode = 2;
