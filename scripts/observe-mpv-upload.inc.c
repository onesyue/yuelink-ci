// Diagnostic only. libmpv invokes these wrappers on its normal render thread.
// Store observations in memory and print them only after the measured loop.
typedef void (APIENTRY *upload_proc)(GLenum, GLint, GLint, GLint, GLsizei,
                                     GLsizei, GLenum, GLenum, const void *);
typedef void (APIENTRY *draw_proc)(GLenum, GLint, GLsizei);
typedef void (APIENTRY *buffer_proc)(GLenum, GLsizeiptr, const void *, GLenum);
typedef void (APIENTRY *image_proc)(GLenum,GLint,GLint,GLsizei,GLsizei,GLint,GLenum,GLenum,const void *);
static upload_proc real_upload;
static draw_proc real_draw;
static buffer_proc real_buffer;
static image_proc real_image;
static int measured_frame = -1;
static uint64_t vertex_hash;
static GLsizeiptr vertex_bytes;
static GLint vertex_buffer;
static float vertex_head[24];
struct image_observation { GLint texture, level, internal_format, width, height; GLenum format, type; };
static struct image_observation images[128];
static unsigned int image_count;
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
    GLint viewport[4], scissor[4], array_buffer, vao;
    GLboolean scissor_on, blend_on, color[4];
    GLboolean raster_discard, depth_on, stencil_on, cull_on, sample_on, alpha_sample_on;
    GLint attribute[4][7], texture[8], sampler[8], sampler_unit[8];
    uintptr_t attribute_offset[4];
    GLint vertex_buffer;
    uint64_t vertex_hash;
    GLsizeiptr vertex_bytes;
    float vertex_head[24];
    GLsizei count;
};
static struct upload_observation uploads[2048];
static struct draw_observation draws[2048];
static unsigned int upload_count, draw_count;

static void APIENTRY track_image(GLenum target,GLint level,GLint internal_format,
                                GLsizei width,GLsizei height,GLint border,
                                GLenum format,GLenum type,const void *pixels) {
    if (target == GL_TEXTURE_2D && image_count < 128) {
        struct image_observation *o = &images[image_count++];
        glGetIntegerv(GL_TEXTURE_BINDING_2D,&o->texture);
        o->level=level; o->internal_format=internal_format; o->width=width; o->height=height;
        o->format=format; o->type=type;
    }
    real_image(target,level,internal_format,width,height,border,format,type,pixels);
}

static void APIENTRY track_buffer(GLenum target, GLsizeiptr size,
                                  const void *data, GLenum usage) {
    if (target == GL_ARRAY_BUFFER && data && size > 0 && size <= 4096) {
        vertex_hash = 1469598103934665603ULL;
        const unsigned char *bytes = data;
        for (GLsizeiptr n = 0; n < size; n++) { vertex_hash ^= bytes[n]; vertex_hash *= 1099511628211ULL; }
        vertex_bytes = size;
        glGetIntegerv(GL_ARRAY_BUFFER_BINDING,&vertex_buffer);
        memset(vertex_head,0,sizeof vertex_head);
        memcpy(vertex_head,data,(size_t)size < sizeof vertex_head ? (size_t)size : sizeof vertex_head);
    }
    real_buffer(target,size,data,usage);
}

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
        glGetIntegerv(GL_VIEWPORT,o->viewport);
        glGetIntegerv(GL_SCISSOR_BOX,o->scissor);
        glGetIntegerv(GL_ARRAY_BUFFER_BINDING,&o->array_buffer);
        glGetIntegerv(0x85B5,&o->vao);
        o->scissor_on = glIsEnabled(GL_SCISSOR_TEST);
        o->blend_on = glIsEnabled(GL_BLEND);
        o->raster_discard = glIsEnabled(0x8C89);
        o->depth_on = glIsEnabled(GL_DEPTH_TEST);
        o->stencil_on = glIsEnabled(GL_STENCIL_TEST);
        o->cull_on = glIsEnabled(GL_CULL_FACE);
        o->sample_on = glIsEnabled(GL_SAMPLE_COVERAGE);
        o->alpha_sample_on = glIsEnabled(GL_SAMPLE_ALPHA_TO_COVERAGE);
        glGetBooleanv(GL_COLOR_WRITEMASK,o->color);
        const GLenum attributes[] = {GL_VERTEX_ATTRIB_ARRAY_ENABLED,GL_VERTEX_ATTRIB_ARRAY_SIZE,GL_VERTEX_ATTRIB_ARRAY_TYPE,GL_VERTEX_ATTRIB_ARRAY_STRIDE,GL_VERTEX_ATTRIB_ARRAY_NORMALIZED,GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING,0x88FE};
        for (int a = 0; a < 4; a++) {
            for (int n = 0; n < 7; n++) glGetVertexAttribiv(a,attributes[n],&o->attribute[a][n]);
            void *offset = NULL;
            glGetVertexAttribPointerv(a,GL_VERTEX_ATTRIB_ARRAY_POINTER,&offset);
            o->attribute_offset[a] = (uintptr_t)offset;
        }
        GLint active;
        glGetIntegerv(GL_ACTIVE_TEXTURE,&active);
        for (int unit = 0; unit < 8; unit++) {
            glActiveTexture(GL_TEXTURE0+unit);
            glGetIntegerv(GL_TEXTURE_BINDING_2D,&o->texture[unit]);
            glGetIntegerv(0x8919,&o->sampler[unit]);
            char name[32];
            snprintf(name,sizeof name,"texture%d",unit);
            GLint location = glGetUniformLocation(o->program,name);
            o->sampler_unit[unit] = -1;
            if (location >= 0) glGetUniformiv(o->program,location,&o->sampler_unit[unit]);
        }
        glActiveTexture(active);
        o->vertex_hash = vertex_hash;
        o->vertex_bytes = vertex_bytes;
        o->vertex_buffer = vertex_buffer;
        memcpy(o->vertex_head,vertex_head,sizeof vertex_head);
    }
    real_draw(mode, first, count);
}

static void print_gpu_observations(void) {
    printf("upload_count=%u draw_count=%u\n", upload_count, draw_count);
    for (unsigned int n = 0; n < image_count; n++) {
        struct image_observation *o = &images[n];
        printf("image texture=%d level=%d internal_format=%d size=%d,%d format=%u type=%u\n",o->texture,o->level,o->internal_format,o->width,o->height,o->format,o->type);
    }
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
        printf("draw_state frame=%d program=%d viewport=%d,%d,%d,%d scissor=%d,%d,%d,%d scissor_on=%u blend_on=%u color=%u,%u,%u,%u array=%d vao=%d",
            o->frame,o->program,o->viewport[0],o->viewport[1],o->viewport[2],o->viewport[3],o->scissor[0],o->scissor[1],o->scissor[2],o->scissor[3],o->scissor_on,o->blend_on,o->color[0],o->color[1],o->color[2],o->color[3],o->array_buffer,o->vao);
        for (int a = 0; a < 4; a++) printf(" attr%d=%d,%d,%d,%d,%d,%d,%d,%llu",a,o->attribute[a][0],o->attribute[a][1],o->attribute[a][2],o->attribute[a][3],o->attribute[a][4],o->attribute[a][5],o->attribute[a][6],(unsigned long long)o->attribute_offset[a]);
        for (int unit = 0; unit < 8; unit++) printf(" unit%d=%d,%d sampler%d=%d",unit,o->texture[unit],o->sampler[unit],unit,o->sampler_unit[unit]);
        printf(" discard_depth_stencil_cull_sample_alpha=%u,%u,%u,%u,%u,%u",o->raster_discard,o->depth_on,o->stencil_on,o->cull_on,o->sample_on,o->alpha_sample_on);
        printf(" vertex_buffer=%d vertex_bytes=%lld vertex_hash=%016llx vertices=",o->vertex_buffer,(long long)o->vertex_bytes,(unsigned long long)o->vertex_hash);
        for (int n = 0; n < 24; n++) printf(n ? ",%.9g" : "%.9g",o->vertex_head[n]);
        printf("\n");
    }
}

static void observe_gpu_at_eof(void) {
    // These reads happen after the original frame count and distinct-pixel
    // result have been fixed. They do not add or replace a measured frame.
    for (unsigned int n = 0; n < draw_count; n++) {
        struct draw_observation *o = &draws[n];
        int latest = 1;
        for (unsigned int k = n + 1; k < draw_count; k++) if (draws[k].fbo == o->fbo) latest = 0;
        if (!latest || !o->fbo || !glIsFramebuffer(o->fbo)) continue;
        int width = o->viewport[2], height = o->viewport[3];
        if (width <= 0 || height <= 0 || width > 64 || height > 64) continue;
        glBindFramebuffer(GL_FRAMEBUFFER,o->fbo);
        GLint format = 0, type = 0, texture = 0;
        GLenum status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
        glGetIntegerv(GL_IMPLEMENTATION_COLOR_READ_FORMAT,&format);
        glGetIntegerv(GL_IMPLEMENTATION_COLOR_READ_TYPE,&type);
        glGetFramebufferAttachmentParameteriv(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_FRAMEBUFFER_ATTACHMENT_OBJECT_NAME,&texture);
        unsigned char data[64*64*4*4] = {0};
        int channels = (format == GL_RGBA || format == 0x80E1) ? 4 : format == 0x8227 ? 2 : format == 0x1903 ? 1 : 0;
        int bytes = type == GL_FLOAT ? 4 : type == GL_UNSIGNED_BYTE ? 1 : type == 0x140B ? 2 : 0;
        uint64_t hash = 1469598103934665603ULL;
        if (status == GL_FRAMEBUFFER_COMPLETE && channels && bytes) {
            glReadPixels(0,0,width,height,format,type,data);
            for (int b = 0; b < width*height*channels*bytes; b++) { hash ^= data[b]; hash *= 1099511628211ULL; }
        }
        printf("gpu_pass fbo=%d program=%d texture=%d size=%d,%d status=%u format=%d type=%d error=%u hash=%016llx first16=",o->fbo,o->program,texture,width,height,status,format,type,glGetError(),(unsigned long long)hash);
        for (int b = 0; b < 16; b++) printf("%02x",data[b]);
        printf("\n");
    }
    glBindFramebuffer(GL_FRAMEBUFFER,0);
    typedef void *(APIENTRY *map_proc)(GLenum,GLintptr,GLsizeiptr,GLbitfield);
    typedef GLboolean (APIENTRY *unmap_proc)(GLenum);
    map_proc map = (map_proc)eglGetProcAddress("glMapBufferRange");
    unmap_proc unmap = (unmap_proc)eglGetProcAddress("glUnmapBuffer");
    if (map && unmap) for (unsigned int n = 0; n < draw_count; n++) {
        struct draw_observation *o = &draws[n];
        int latest = 1;
        for (unsigned int k = n + 1; k < draw_count; k++) if (draws[k].vertex_buffer == o->vertex_buffer) latest = 0;
        if (!latest || !o->vertex_buffer || o->vertex_bytes <= 0 || o->vertex_bytes > 4096 || !glIsBuffer(o->vertex_buffer)) continue;
        glBindBuffer(0x8F36,o->vertex_buffer);
        const unsigned char *data = map(0x8F36,0,o->vertex_bytes,1);
        uint64_t hash = 1469598103934665603ULL;
        GLboolean unmapped = 0;
        if (data) {
            for (GLsizeiptr b = 0; b < o->vertex_bytes; b++) { hash ^= data[b]; hash *= 1099511628211ULL; }
            unmapped = unmap(0x8F36);
        }
        printf("gpu_vertex buffer=%d bytes=%lld mapped=%d unmapped=%u error=%u hash=%016llx cpu_hash=%016llx\n",o->vertex_buffer,(long long)o->vertex_bytes,data != NULL,unmapped,glGetError(),(unsigned long long)hash,(unsigned long long)o->vertex_hash);
    }
    glBindBuffer(0x8F36,0);
    GLuint fbo;
    glGenFramebuffers(1,&fbo);
    for (unsigned int n = 0; n < upload_count; n++) {
        struct upload_observation *o = &uploads[n];
        int latest = 1;
        for (unsigned int k = n + 1; k < upload_count; k++) if (uploads[k].texture == o->texture) latest = 0;
        if (!latest || o->format != 0x1903 || o->width > 64 || o->height > 64) continue;
        glBindFramebuffer(GL_FRAMEBUFFER,fbo);
        glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,o->texture,0);
        GLenum status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
        unsigned char data[64*64*4] = {0};
        uint64_t hash = 1469598103934665603ULL;
        if (status == GL_FRAMEBUFFER_COMPLETE) {
            glReadPixels(0,0,o->width,o->height,GL_RGBA,GL_UNSIGNED_BYTE,data);
            for (int p = 0; p < o->width * o->height; p++) { hash ^= data[p*4]; hash *= 1099511628211ULL; }
        }
        printf("gpu_plane texture=%d size=%d,%d status=%u error=%u red_hash=%016llx cpu_hash=%016llx first=%u\n",o->texture,o->width,o->height,status,glGetError(),(unsigned long long)hash,(unsigned long long)o->hash,data[0]);
    }
    glBindFramebuffer(GL_FRAMEBUFFER,0);
    glDeleteFramebuffers(1,&fbo);
    for (unsigned int n = 0; n < draw_count; n++) {
        GLuint program = draws[n].program;
        int first = 1;
        for (unsigned int k = 0; k < n; k++) if (draws[k].program == (GLint)program) first = 0;
        if (!first || !glIsProgram(program)) continue;
        GLuint shaders[8] = {0};
        GLsizei shader_count = 0;
        glGetAttachedShaders(program,8,&shader_count,shaders);
        for (GLsizei s = 0; s < shader_count; s++) {
            GLint length = 0, type = 0;
            glGetShaderiv(shaders[s],GL_SHADER_SOURCE_LENGTH,&length);
            glGetShaderiv(shaders[s],GL_SHADER_TYPE,&type);
            if (length <= 0 || length > 131072) continue;
            char *source = malloc((size_t)length);
            if (!source) continue;
            GLsizei actual = 0;
            glGetShaderSource(shaders[s],length,&actual,source);
            uint64_t hash = 1469598103934665603ULL;
            for (GLsizei b = 0; b < actual; b++) { hash ^= (unsigned char)source[b]; hash *= 1099511628211ULL; }
            printf("shader program=%u type=%d bytes=%d hash=%016llx error=%u\n",program,type,actual,(unsigned long long)hash,glGetError());
            free(source);
        }
        GLint uniforms = 0;
        glGetProgramiv(program,GL_ACTIVE_UNIFORMS,&uniforms);
        for (GLint u = 0; u < uniforms; u++) {
            char name[256] = {0};
            GLsizei length = 0;
            GLint size = 0;
            GLenum type = 0;
            glGetActiveUniform(program,u,sizeof name,&length,&size,&type,name);
            GLint location = glGetUniformLocation(program,name);
            if (location < 0 || size != 1) continue;
            GLfloat values[16] = {0};
            glGetUniformfv(program,location,values);
            printf("uniform program=%u name=%s type=%u values=",program,name,type);
            for (int v = 0; v < 16; v++) printf(v ? ",%.9g" : "%.9g",values[v]);
            printf(" error=%u\n",glGetError());
        }
    }
}
