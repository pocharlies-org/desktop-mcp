// Punto de entrada estable para TCC. Hace FORK, no exec: el proceso del bundle
// sigue vivo y el python hijo hereda su "responsible process", asi que macOS
// atribuye Grabacion de pantalla / Accesibilidad a MacDesktopMCP.app.
// Con execv el proceso DEJA de ser el bundle (pasa a ser el binario de python de
// Homebrew) y el permiso concedido a la app no aplica: "could not create image
// from display" aunque el interruptor este activado.
#include <ApplicationServices/ApplicationServices.h>
#include <errno.h>
#include <signal.h>
#include <time.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

static pid_t child = -1;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(void) {
    const char *home = getenv("HOME");
    if (!home) return 1;
    static char boot[1024];
    snprintf(boot, sizeof boot, "%s/.local/share/desktop-mcp/boot.sh", home);

    // Diagnostic: does the BUNDLE PROCESS itself see the capture grant? Checking
    // this from a child process tells you nothing about the bundle's own identity.
    {
        static char lg[1024];
        snprintf(lg, sizeof lg, "%s/.local/share/desktop-mcp/desktop-mcp.log", home);
        FILE *f = fopen(lg, "a");
        if (f) {
            time_t t = time(NULL);
            char ts[32];
            strftime(ts, sizeof ts, "%F %T", localtime(&t));
            fprintf(f, "%s pid=%d BUNDLE preflight_screen_capture=%s ax_trusted=%s\n",
                    ts, (int)getpid(),
                    CGPreflightScreenCaptureAccess() ? "true" : "false",
                    AXIsProcessTrusted() ? "true" : "false");
            fclose(f);
        }
    }

    child = fork();
    if (child < 0) return 1;
    if (child == 0) {
        char *args[] = {"/bin/bash", boot, NULL};
        execv(args[0], args);
        _exit(127);
    }
    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    int st = 0;
    while (waitpid(child, &st, 0) < 0) {
        if (errno != EINTR) return 1;
    }
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
