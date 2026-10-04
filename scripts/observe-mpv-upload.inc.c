// Diagnostic only. libmpv invokes these wrappers on its normal render thread.
// Store observations in memory and print them only after the measured loop.
typedef void (APIENTRY *upload_proc)(GLenum, GLint, GLint, GLint, GLsizei,
                                     GLsizei, GLenum, GLenum, const void *);
typedef void (APIENTRY *draw_proc)(GLenum, GLint, GLsizei);
static upload_proc real_upload;
static draw_proc real_draw;
static int measured_frame = -1;
struct upload_observation {
    int frame;
    GLenum target, format, type;
    GLint texture, width, height, row_length, alignment, pbo, skip_rows, skip_pixels;
    uint64_t hash;
    unsigned int first;
};
struct draw_observation {
    int frame;
    GLenum mode;
    GLint program, fbo, first;
    GLsizei count;
};
static struct upload_observation uploads[2048];
static struct draw_observation draws[2048];
static unsigned int upload_count, draw_count;

static void APIENTRY track_upload(GLenum target, GLint level, GLint x, GLint y,
                                  GLsizei width, GLsizei height, GLenum format,
                                  GLenum type, const void *pixels) {
    if (upload_count < 2048) {
        struct upload_observation *o = &uploads[upload_count++];
        o->frame = measured_frame; o->target = target;
        o->width = width; o->height = height; o->format = format; o->type = type;
        glGetIntegerv(GL_TEXTURE_BINDING_2D, &o->texture);
        glGetIntegerv(0x0CF2, &o->row_length);
        glGetIntegerv(GL_UNPACK_ALIGNMENT, &o->alignment);
        glGetIntegerv(0x88EF, &o->pbo);
        glGetIntegerv(0x0CF3, &o->skip_rows);
        glGetIntegerv(0x0CF4, &o->skip_pixels);
        int channels = (format == 0x1903 || format == GL_LUMINANCE) ? 1 :
            (format == 0x8227 || format == GL_LUMINANCE_ALPHA) ? 2 :
            format == GL_RGB ? 3 : format == GL_RGBA ? 4 : 0;
        if (pixels && !o->pbo && type == GL_UNSIGNED_BYTE && channels &&
            width > 0 && width <= 64 && height > 0 && height <= 64 &&
            o->alignment > 0 && o->alignment <= 8 &&
            o->row_length >= 0 && o->row_length <= 4096 &&
            o->skip_rows == 0 && o->skip_pixels == 0) {
            size_t row_bytes = (size_t)(o->row_length ? o->row_length : width) * channels;
            size_t stride = (row_bytes + o->alignment - 1) / o->alignment * o->alignment;
            const unsigned char *bytes = pixels;
            o->first = bytes[0];
            o->hash = 1469598103934665603ULL;
            for (int row = 0; row < height; row++) {
                for (int col = 0; col < width * channels; col++) {
                    o->hash ^= bytes[row * stride + col];
                    o->hash *= 1099511628211ULL;
                }
            }
        }
    }
    real_upload(target, level, x, y, width, height, format, type, pixels);
}

static void APIENTRY track_draw(GLenum mode, GLint first, GLsizei count) {
    if (draw_count < 2048) {
        struct draw_observation *o = &draws[draw_count++];
        o->frame = measured_frame; o->mode = mode; o->first = first; o->count = count;
        glGetIntegerv(GL_CURRENT_PROGRAM, &o->program);
        glGetIntegerv(GL_FRAMEBUFFER_BINDING, &o->fbo);
    }
    real_draw(mode, first, count);
}

static void print_gpu_observations(void) {
    printf("upload_count=%u draw_count=%u\n", upload_count, draw_count);
    for (unsigned int n = 0; n < upload_count; n++) {
        struct upload_observation *o = &uploads[n];
        printf("upload frame=%d target=%u texture=%d size=%d,%d format=%u type=%u row_length=%d alignment=%d pbo=%d skip=%d,%d hash=%016llx first=%u\n",
            o->frame,o->target,o->texture,o->width,o->height,o->format,o->type,
            o->row_length,o->alignment,o->pbo,o->skip_rows,o->skip_pixels,
            (unsigned long long)o->hash,o->first);
    }
    for (unsigned int n = 0; n < draw_count; n++) {
        struct draw_observation *o = &draws[n];
        printf("draw frame=%d program=%d fbo=%d mode=%u first=%d count=%d\n",
            o->frame,o->program,o->fbo,o->mode,o->first,o->count);
    }
}
