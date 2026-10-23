package dev.hitl.demo;

import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.blankOrNullString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest(properties = {
    "spring.datasource.url=jdbc:h2:mem:demo;MODE=PostgreSQL;DB_CLOSE_DELAY=-1;NON_KEYWORDS=VALUE",
    "spring.datasource.username=sa", "spring.datasource.password=", "spring.datasource.driver-class-name=org.h2.Driver"
})
@AutoConfigureMockMvc
class DemoApiTest {
    @Autowired MockMvc mvc;

    @Test void writesAndReadsPersistentRecords() throws Exception {
        mvc.perform(post("/api/records").contentType(MediaType.APPLICATION_JSON)
                .content("{\"value\":\"survives-backend-adjust\"}"))
            .andExpect(status().isCreated())
            .andExpect(jsonPath("$.id").isNumber())
            .andExpect(jsonPath("$.value").value("survives-backend-adjust"))
            .andExpect(jsonPath("$.createdAt").exists());
        mvc.perform(get("/api/records"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$[0].value").value("survives-backend-adjust"));
    }

    @Test void rejectsBlankAndOversizedValues() throws Exception {
        for (String value : new String[]{"  ", "x".repeat(501)}) {
            mvc.perform(post("/api/records").contentType(MediaType.APPLICATION_JSON)
                    .content("{\"value\":\"" + value + "\"}"))
                .andExpect(status().isBadRequest());
        }
    }

    @Test void reportsDatabaseReadiness() throws Exception {
        mvc.perform(get("/health"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.status").value("UP"))
            .andExpect(jsonPath("$.database").value("UP"));
    }

    @Test void exposesInstanceForLoadBalancingEvidence() throws Exception {
        mvc.perform(get("/api/instance"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.instance", not(blankOrNullString())));
    }
}
