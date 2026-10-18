/* Truy cập JSON an toàn: sai kiểu / thiếu trường thì coi như không có (không bịa giá trị). */
#ifndef LM_JSON_H
#define LM_JSON_H
#include "cjson/cJSON.h"
#include <string.h>

static inline cJSON *jget(const cJSON *o, const char *k) {
    return cJSON_IsObject(o) ? cJSON_GetObjectItemCaseSensitive((cJSON *)o, k) : NULL;
}
static inline int jnum(const cJSON *o, const char *k, double *out) {
    cJSON *v = jget(o, k);
    if (!cJSON_IsNumber(v)) return 0;
    *out = v->valuedouble;
    return 1;
}
static inline double jnumd(const cJSON *o, const char *k, double dflt) {
    double v;
    return jnum(o, k, &v) ? v : dflt;
}
static inline const char *jstr(const cJSON *o, const char *k) {
    cJSON *v = jget(o, k);
    return cJSON_IsString(v) && v->valuestring ? v->valuestring : NULL;
}
static inline const char *jstrd(const cJSON *o, const char *k, const char *dflt) {
    const char *s = jstr(o, k);
    return s ? s : dflt;
}
static inline int jbool(const cJSON *o, const char *k, int *out) {
    cJSON *v = jget(o, k);
    if (!cJSON_IsBool(v)) return 0;
    *out = cJSON_IsTrue(v);
    return 1;
}
/* true nếu trường là bool true; thiếu/sai kiểu -> dflt */
static inline int jbool_d(const cJSON *o, const char *k, int dflt) {
    int v;
    return jbool(o, k, &v) ? v : dflt;
}
static inline cJSON *jarr(const cJSON *o, const char *k) {
    cJSON *v = jget(o, k);
    return cJSON_IsArray(v) ? v : NULL;
}
static inline int jarr_n(const cJSON *o, const char *k) {
    cJSON *a = jarr(o, k);
    return a ? cJSON_GetArraySize(a) : 0;
}
#endif
