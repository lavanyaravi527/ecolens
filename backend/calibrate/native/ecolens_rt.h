/*
 * ecolens_rt.h - tiny runtime shared by the calibration kernels and by the
 * programs EcoLens generates from your mini-language.
 *
 * It provides: a monotonic clock, Linux RAPL energy-counter reading (the
 * counters are passed in on the command line by the Python side) and a
 * harness that repeats a function until enough time has elapsed to get a
 * trustworthy energy reading.
 *
 * Requires gcc or clang (uses inline asm as an optimisation barrier).
 */
#ifndef ECOLENS_RT_H
#define ECOLENS_RT_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* Forces `x` to live in a register at this point, emits no instruction. */
#define ECO_BAR(x) __asm__ volatile("" : "+r"(x))

static double eco_now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

/* ---------- RAPL (Linux powercap) ---------- */

#define ECO_MAX_DOM 16
static const char *eco_dom_path[ECO_MAX_DOM];
static uint64_t eco_dom_max[ECO_MAX_DOM];
static int eco_ndom = 0;

/* Reads every "--rapl path:maxrange_uj" pair from argv. */
static void eco_rapl_parse(int argc, char **argv) {
    for (int i = 1; i < argc - 1; i++) {
        if (strcmp(argv[i], "--rapl") != 0) continue;
        char *s = argv[i + 1];
        char *colon = strrchr(s, ':');
        if (!colon || eco_ndom >= ECO_MAX_DOM) continue;
        *colon = 0;
        eco_dom_path[eco_ndom] = s;
        eco_dom_max[eco_ndom] = strtoull(colon + 1, NULL, 10);
        eco_ndom++;
    }
}

/* Returns 1 when all domains were read, 0 otherwise. Values are in microjoules. */
static int eco_rapl_read(uint64_t *out) {
    if (eco_ndom == 0) return 0;
    for (int i = 0; i < eco_ndom; i++) {
        FILE *f = fopen(eco_dom_path[i], "r");
        if (!f) return 0;
        unsigned long long v = 0;
        int ok = fscanf(f, "%llu", &v);
        fclose(f);
        if (ok != 1) return 0;
        out[i] = v;
    }
    return 1;
}

static double eco_rapl_joules(const uint64_t *a, const uint64_t *b) {
    double uj = 0;
    for (int i = 0; i < eco_ndom; i++) {
        uint64_t d = b[i] >= a[i] ? b[i] - a[i] : b[i] + eco_dom_max[i] - a[i];
        uj += (double)d;
    }
    return uj * 1e-6;
}

static void eco_print_energy(double joules, int have) {
    if (have) printf("\"joules\":%.9g", joules);
    else printf("\"joules\":null");
}

/* ---------- harness for generated programs ---------- */

/*
 * argv: <target_seconds> [--rapl path:max ...]
 * Runs fn() repeatedly for roughly target_seconds and prints one JSON line.
 */
__attribute__((unused)) static int eco_harness(int64_t (*fn)(void), int argc, char **argv) {
    double target = argc > 1 ? atof(argv[1]) : 0.5;
    if (target <= 0) target = 0.5;
    eco_rapl_parse(argc, argv);

    int64_t res = fn();            /* warm-up, also gives the return value */
    uint64_t runs = 1;
    double t;
    for (;;) {
        double t0 = eco_now();
        for (uint64_t i = 0; i < runs; i++) res = fn();
        t = eco_now() - t0;
        if (t >= 0.05) break;
        runs *= 4;
    }
    runs = (uint64_t)((double)runs * target / t) + 1;

    uint64_t e0[ECO_MAX_DOM], e1[ECO_MAX_DOM];
    int have = eco_rapl_read(e0);
    double t0 = eco_now();
    for (uint64_t i = 0; i < runs; i++) res = fn();
    double t1 = eco_now();
    have = have && eco_rapl_read(e1);

    printf("{\"runs\":%llu,\"seconds\":%.9g,", (unsigned long long)runs, t1 - t0);
    eco_print_energy(have ? eco_rapl_joules(e0, e1) : 0, have);
    printf(",\"result\":%lld}\n", (long long)res);
    return 0;
}

#endif
