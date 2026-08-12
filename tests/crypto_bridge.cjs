const { decryptCredential, encryptCredential } = require('../credential-crypto');

const [command, key, payload = ''] = process.argv.slice(2);
if (command === 'encrypt') process.stdout.write(encryptCredential(payload, key));
else if (command === 'decrypt') process.stdout.write(decryptCredential(payload, key));
else process.exitCode = 2;
