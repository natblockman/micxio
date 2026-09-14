# Install micxio

## Packaged Windows version

1. Download the Windows ZIP file from Gumroad.
2. Right-click the ZIP file and choose **Extract All**. Do not run the app from
   inside the ZIP file.
3. Open the extracted `micxio` folder.
4. Double-click `micxio.exe` to start the app.
5. If Windows SmartScreen appears, select **More info** and then **Run anyway**
   only when you downloaded the app from the official seller.

## Packaged Linux version

1. Download and extract the Linux ZIP file.
2. Open the extracted `micxio` folder.
3. Double-click `micxio`, or run this command in a terminal from that folder:

   ```bash
   ./micxio
   ```

4. If the file is not executable, run:

   ```bash
   chmod +x micxio
   ./micxio
   ```

## First launch

micxio may download its audio-processing engine the first time it needs it.
After that, normal audio conversion works offline. For recording or Live Voice,
allow microphone access when your operating system asks for permission.

## Running from source code

### Windows

Install Python 3, then double-click `run.bat`.

### Linux

Open a terminal in this folder and run:

```bash
./run.sh
```
