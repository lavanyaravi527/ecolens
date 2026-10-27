/*
 * kernels.c - EcoLens calibration micro-benchmarks.
 *
 * Every kernel is a tight loop. Python runs them one after another, reads the
 * energy counter around each run and takes DIFFERENCES between kernels that
 * share the same structure, so that loop overhead and the xor that keeps the
 * data from collapsing cancel out.
 *
 * Usage: kernels <name> <target_seconds> <buffer_bytes> [--rapl path:max ...]
 * Compile with: cc -O2 kernels.c -o kernels
 */
#include "ecolens_rt.h"

#define K4(OPX) do { \
    a0 = OPX(a0); ECO_BAR(a0); a1 = OPX(a1); ECO_BAR(a1); \
    a2 = OPX(a2); ECO_BAR(a2); a3 = OPX(a3); ECO_BAR(a3); } while (0)
#define K32(OPX) do { K4(OPX); K4(OPX); K4(OPX); K4(OPX); \
                      K4(OPX); K4(OPX); K4(OPX); K4(OPX); } while (0)

#define ZV 0xD6E8FEB86659FD93ULL

#define DEF_COMPUTE(NAME, YV, OPX) \
static uint64_t NAME(uint64_t n, void *ctx) { \
    (void)ctx; \
    uint64_t y = (YV), z = ZV; \
    uint64_t a0 = 0x1111111111111111ULL, a1 = 0x2222222222222223ULL, \
             a2 = 0x3333333333333335ULL, a3 = 0x4444444444444447ULL; \
    ECO_BAR(y); ECO_BAR(z); \
    for (uint64_t i = 0; i < n; i++) { K32(OPX); } \
    return a0 ^ a1 ^ a2 ^ a3; \
}

#define OP_XOR(a) ((a) ^ z)
#define OP_ADD(a) (((a) + y) ^ z)
#define OP_AND(a) (((a) & y) ^ z)
#define OP_SHL(a) (((a) << 5) ^ z)
#define OP_SHR(a) (((a) >> 3) ^ z)
#define OP_MUL(a) (((a) * y) ^ z)
#define OP_DIV(a) (((a) / y) ^ z)
#define OP_REM(a) (((a) % y) ^ z)

DEF_COMPUTE(k_xor, 0x1234567ULL, OP_XOR)
DEF_COMPUTE(k_add, 0x1234567ULL, OP_ADD)
DEF_COMPUTE(k_and, 0xF0F0F0F0F0F0F0F1ULL, OP_AND)
DEF_COMPUTE(k_shl, 0, OP_SHL)
DEF_COMPUTE(k_shr, 0, OP_SHR)
DEF_COMPUTE(k_mul, 0x9E3779B97F4A7C15ULL, OP_MUL)
DEF_COMPUTE(k_div, 7, OP_DIV)
DEF_COMPUTE(k_rem, 0x7FFFFFFFFFFFFFFFULL, OP_REM)

/* register-to-register move (dependent), only where we know the mnemonic */
#if defined(__x86_64__) || defined(__i386__)
#  define EMIT_MOV(t, a) __asm__ volatile("mov %1, %0" : "=r"(t) : "r"(a))
#  define HAVE_MOV 1
#elif defined(__aarch64__)
#  define EMIT_MOV(t, a) __asm__ volatile("mov %0, %1" : "=r"(t) : "r"(a))
#  define HAVE_MOV 1
#endif
#ifdef HAVE_MOV
#  define OP_MOV(a) ({ uint64_t t_; EMIT_MOV(t_, a); t_ ^ z; })
DEF_COMPUTE(k_mov, 0, OP_MOV)
#endif

/* the empty loop: n iterations of nothing -> cost of the loop itself */
static uint64_t k_empty(uint64_t n, void *ctx) {
    (void)ctx;
    for (uint64_t i = 0; i < n; i++) __asm__ volatile("");
    return n;
}

/* ---------- memory kernels ---------- */

typedef struct { uint32_t *p; uint64_t mask; } mem_ctx;

/* one sequential 4-byte load per iteration (wraps inside the buffer) */
static uint64_t k_seq(uint64_t n, void *vc) {
    mem_ctx *c = (mem_ctx *)vc;
    const uint32_t *p = c->p; uint64_t m = c->mask, s = 0;
    for (uint64_t i = 0; i < n; i++) { s ^= p[i & m]; ECO_BAR(s); }
    return s;
}

/* same instruction mix, but the value comes from a register (no load) */
static uint64_t k_seq_reg(uint64_t n, void *vc) {
    mem_ctx *c = (mem_ctx *)vc;
    uint64_t m = c->mask, s = 0;
    for (uint64_t i = 0; i < n; i++) { uint64_t v = i & m; ECO_BAR(v); s ^= v; ECO_BAR(s); }
    return s;
}

/* independent random 4-byte loads (xorshift64 index, no dependence on data) */
static uint64_t k_rand(uint64_t n, void *vc) {
    mem_ctx *c = (mem_ctx *)vc;
    const uint32_t *p = c->p; uint64_t m = c->mask, s = 0, x = 88172645463325252ULL;
    for (uint64_t i = 0; i < n; i++) {
        x ^= x << 13; x ^= x >> 7; x ^= x << 17;
        s ^= p[x & m]; ECO_BAR(s);
    }
    return s;
}

typedef uint64_t (*kfn)(uint64_t, void *);
typedef struct { const char *name; kfn fn; int per_iter; int mem; } entry;

static const entry TABLE[] = {
    {"empty", k_empty, 1, 0}, {"xor", k_xor, 32, 0}, {"add", k_add, 32, 0},
    {"and", k_and, 32, 0}, {"shl", k_shl, 32, 0}, {"shr", k_shr, 32, 0},
    {"mul", k_mul, 32, 0}, {"div", k_div, 32, 0}, {"rem", k_rem, 32, 0},
#ifdef HAVE_MOV
    {"mov", k_mov, 32, 0},
#endif
    {"seq", k_seq, 1, 1}, {"seq_reg", k_seq_reg, 1, 1}, {"rand", k_rand, 1, 1},
};

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: kernels name target_s bytes [--rapl p:m]\n"); return 2; }
    const char *name = argv[1];
    double target = atof(argv[2]);
    uint64_t bytes = strtoull(argv[3], NULL, 10);
    eco_rapl_parse(argc, argv);

    const entry *e = NULL;
    for (size_t i = 0; i < sizeof TABLE / sizeof *TABLE; i++)
        if (!strcmp(TABLE[i].name, name)) e = &TABLE[i];
    if (!e) { fprintf(stderr, "unknown or unsupported kernel %s\n", name); return 3; }

    mem_ctx c = {0, 0};
    if (e->mem) {
        if (bytes < 4096 || (bytes & (bytes - 1))) { fprintf(stderr, "bytes must be a power of two >= 4096\n"); return 4; }
        c.p = (uint32_t *)malloc(bytes);
        if (!c.p) { fprintf(stderr, "out of memory\n"); return 5; }
        for (uint64_t i = 0; i < bytes / 4; i++) c.p[i] = (uint32_t)(i * 2654435761u);  /* commit pages */
        c.mask = bytes / 4 - 1;
    }

    /* find an iteration count that runs for ~target seconds */
    uint64_t n = 1024; double t;
    for (;;) {
        double t0 = eco_now(); volatile uint64_t sink = e->fn(n, &c); (void)sink;
        t = eco_now() - t0;
        if (t >= 0.02) break;
        n *= 4;
    }
    n = (uint64_t)((double)n * target / t) + 1;

    uint64_t e0[ECO_MAX_DOM], e1[ECO_MAX_DOM];
    int have = eco_rapl_read(e0);
    double t0 = eco_now();
    volatile uint64_t sink = e->fn(n, &c);
    double t1 = eco_now();
    have = have && eco_rapl_read(e1);

    printf("{\"kernel\":\"%s\",\"bytes\":%llu,\"n\":%llu,\"per_iter\":%d,\"seconds\":%.9g,",
           name, (unsigned long long)bytes, (unsigned long long)n, e->per_iter, t1 - t0);
    eco_print_energy(have ? eco_rapl_joules(e0, e1) : 0, have);
    printf(",\"sink\":%llu}\n", (unsigned long long)sink);
    return 0;
}
